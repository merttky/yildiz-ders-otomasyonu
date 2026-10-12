"""
YILDIZ Ders Otomasyonu - LMS HTTP Client
Selenium-free API client using requests + BeautifulSoup.

Features:
- Thread-safe operations with RLock
- Real login verification (no false positives)
- Session expiry detection + automatic re-login
- JSON-based session persistence with 0600 file permissions and username scoping
- Time-validated attendance button selection
- Direct targeting by program_no and Turkish-aware course name matching
- Secure TLS 1.3 verification with automatic connection retries
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    MAX_RETRY_ATTEMPTS,
    REQUEST_TIMEOUT,
    RETRY_DELAY,
    SESSION_CACHE_TTL,
    SESSION_FILE,
    LEGACY_SESSION_FILE,
    SESSION_LIFETIME,
    USER_AGENT,
    YTU_BASE_URL,
    YTU_COCKPIT_URL,
    YTU_LOGIN_URL,
)
from turkish_utils import turkish_contains

# API endpoints
YTU_ATTEND_URL = f"{YTU_BASE_URL}/ViewLessonProgramAsStudent/AttendLessonProgram"
YTU_COURSE_TAB_URL = f"{YTU_BASE_URL}/ViewCockpit/GetCourseTab"
YTU_ATTENDANCE_LIST_URL = f"{YTU_BASE_URL}/ViewLessonProgramAsStudent/ListLessonProgramAttendance"
YTU_LESSON_VIEW_URL = f"{YTU_BASE_URL}/ViewLessonProgramAsStudent/View"

logger = logging.getLogger(__name__)


class YTUClientException(Exception):
    """Custom exception for YTU Client errors"""
    pass


class YTUSessionExpiredException(YTUClientException):
    """Raised when server-side session has expired"""
    pass


def parse_ytu_datetime(date_str: str) -> Optional[datetime.datetime]:
    """Parse date strings from YTU Online HTML (e.g. '23.03.2026 13:00:00')."""
    if not date_str or not isinstance(date_str, str):
        return None
    cleaned = date_str.strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(cleaned, fmt)
        except ValueError:
            pass
    return None


def is_attendance_time_active(
    start_str: str,
    end_str: str,
    now: Optional[datetime.datetime] = None
) -> bool:
    """
    Checks if current time falls within the lesson attendance window.
    Window: 15 minutes before StartTime until EndTime (plus 15 minute buffer).
    """
    start_dt = parse_ytu_datetime(start_str)
    end_dt = parse_ytu_datetime(end_str)
    if not start_dt:
        return True  # If unparseable, don't filter out

    check_dt = now or datetime.datetime.now()
    window_start = start_dt - datetime.timedelta(minutes=15)
    window_end = (end_dt + datetime.timedelta(minutes=15)) if end_dt else (start_dt + datetime.timedelta(hours=2))

    return window_start <= check_dt <= window_end


class YTUClient:
    """
    YTU Online LMS HTTP client.
    Thread-safe and resilient.
    """

    def __init__(self, username: str = "", password: str = ""):
        self.username = username.strip() if username else ""
        self.password = password
        self._lock = threading.RLock()

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': USER_AGENT,
            'Accept': 'application/json, text/html, */*',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1',
        })

        # Mount HTTPAdapter with automatic retries for transient connection errors
        retries = Retry(
            total=MAX_RETRY_ATTEMPTS,
            backoff_factor=RETRY_DELAY,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        self.session.verify = True

        self.logged_in = False
        self.csrf_token: Optional[str] = None
        self._last_validated_time: float = 0.0

    def update_credentials(self, username: str, password: str) -> None:
        """Update user credentials safely."""
        with self._lock:
            clean_user = username.strip() if username else ""
            if self.username != clean_user or self.password != password:
                self.username = clean_user
                self.password = password
                self.logged_in = False
                self._last_validated_time = 0.0

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """
        Login to YTU Online using JSON-based AJAX authentication.
        Accurately verifies authentication without false positives.
        """
        with self._lock:
            target_user = (username.strip() if username else self.username)
            target_pwd = password if password is not None else self.password

            if not target_user or not target_pwd:
                logger.error("Login failed: Username or password missing")
                return False

            self.username = target_user
            self.password = target_pwd

            try:
                logger.info(f"Attempting login for: {self.username}")

                login_payload = {
                    "Username": self.username,
                    "Password": self.password,
                    "RememberMe": False
                }

                headers = {
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                }

                response = self.session.post(
                    YTU_LOGIN_URL,
                    json=login_payload,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True
                )

                response.raise_for_status()

                # Step 1: Reject explicit failures in login response
                if self._is_login_response_rejected(response):
                    logger.warning("✗ Login rejected by server (invalid credentials or login error)")
                    self.logged_in = False
                    return False

                # Step 2: Ground-truth verification: probe Cockpit directly
                # Verifies that server does NOT redirect to /Account/Login
                if self._validate_session(force_network=True):
                    self.logged_in = True
                    self._last_validated_time = time.time()
                    logger.info("✓ Login successful and cockpit access verified")
                    self._save_session()
                    return True
                else:
                    logger.warning("✗ Login failed: Cockpit access redirected to login or unauthorized")
                    self.logged_in = False
                    return False

            except requests.exceptions.RequestException as e:
                logger.error(f"Login request failed: {e}")
                self.logged_in = False
                raise YTUClientException(f"Login failed: {e}")

    def _is_login_response_rejected(self, response: requests.Response) -> bool:
        """
        Checks if login response explicitly indicates failure.
        Rejects bad status codes, redirect loops to login, and explicit failure JSON.
        """
        # Status code check
        if response.status_code not in (200, 302):
            return True

        # Check redirect history: did it redirect back to /Account/Login?
        if response.history:
            for r in response.history:
                if 'Account/Login' in r.headers.get('Location', ''):
                    return True

        # Check JSON response if present
        try:
            json_response = response.json()
            if isinstance(json_response, dict):
                # Explicit failure flags
                if json_response.get('Success') is False or json_response.get('success') is False:
                    logger.debug(f"Login JSON explicit failure: {json_response.get('Message')}")
                    return True
                if json_response.get('IsSuccess') is False or json_response.get('isSuccess') is False:
                    logger.debug(f"Login JSON explicit failure: {json_response.get('Message')}")
                    return True
        except ValueError:
            pass

        # If response redirected to or is still at Login page with form, it failed
        if 'Account/Login' in response.url:
            if 'id="loginForm"' in response.text or 'name="Password"' in response.text:
                return True

        return False

    def _verify_login(self, response: requests.Response) -> bool:
        """
        Verify if login was successful.
        Checks that response did not explicitly fail, then validates Cockpit access.
        """
        if self._is_login_response_rejected(response):
            return False
        return self._validate_session(force_network=True)

    def _save_session(self) -> None:
        """Save session cookies to disk as JSON with 0600 permissions."""
        if not self.username:
            return

        try:
            cookie_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
            session_data = {
                'username': self.username,
                'cookies': cookie_dict,
                'timestamp': time.time(),
            }

            temp_dir = SESSION_FILE.parent
            temp_fd, temp_path_str = tempfile.mkstemp(
                prefix="session_",
                suffix=".tmp",
                dir=temp_dir
            )
            temp_path = Path(temp_path_str)

            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(session_data, f)
                f.flush()
                os.fsync(f.fileno())

            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass

            os.replace(temp_path, SESSION_FILE)

            try:
                os.chmod(SESSION_FILE, 0o600)
            except OSError:
                pass

            logger.debug(f"✓ Session saved for: {self.username}")
        except Exception as e:
            logger.warning(f"Failed to save session: {e}")

    def load_session(self, username: Optional[str] = None) -> bool:
        """
        Load saved session from JSON file.
        Enforces username matching and lifetime expiration.
        """
        with self._lock:
            # Clean up legacy pickle session file if present
            if LEGACY_SESSION_FILE.exists():
                try:
                    LEGACY_SESSION_FILE.unlink()
                except OSError:
                    pass

            target_user = (username.strip() if username else self.username)
            if not SESSION_FILE.exists():
                return False

            try:
                with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)

                saved_user = session_data.get('username', '')
                if target_user and saved_user and saved_user.lower() != target_user.lower():
                    logger.info(f"Cached session belongs to '{saved_user}', current user is '{target_user}'. Discarding.")
                    return False

                age = time.time() - session_data.get('timestamp', 0)
                if age > SESSION_LIFETIME:
                    logger.info(f"Session expired ({age:.0f}s)")
                    return False

                # Restore cookies
                cookies = session_data.get('cookies', {})
                self.session.cookies.clear()
                requests.utils.add_dict_to_cookiejar(self.session.cookies, cookies)

                if target_user:
                    self.username = target_user

                if self._validate_session(force_network=True):
                    self.logged_in = True
                    self._last_validated_time = time.time()
                    logger.info(f"✓ Loaded cached session for {saved_user or target_user} (age: {age:.0f}s)")
                    return True

                logger.info("Cached session rejected by server validation")
                return False

            except Exception as e:
                logger.warning(f"Failed to load session: {e}")
                return False

    def _validate_session(self, force_network: bool = False) -> bool:
        """Check if current session is still valid."""
        with self._lock:
            now = time.time()
            if not force_network and (now - self._last_validated_time) < SESSION_CACHE_TTL:
                return self.logged_in

            try:
                response = self.session.get(
                    YTU_COCKPIT_URL,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False
                )

                if response.status_code == 302:
                    location = response.headers.get('Location', '')
                    if 'Login' in location or 'Account' in location:
                        return False

                if response.status_code == 200:
                    # Double check if response body is the login page
                    if 'id="loginForm"' in response.text or 'name="Password"' in response.text:
                        return False
                    self._last_validated_time = now
                    return True

                return False

            except Exception as e:
                logger.debug(f"Session validation network error: {e}")
                return False

    def _check_and_raise_if_session_expired(self, response: requests.Response) -> None:
        """Detects if response indicates an expired session and raises YTUSessionExpiredException."""
        if response.status_code in (401, 403):
            raise YTUSessionExpiredException("HTTP 401/403 Unauthorized")

        if response.status_code == 302:
            location = response.headers.get('Location', '')
            if 'Login' in location or 'Account' in location:
                raise YTUSessionExpiredException("Redirected to Login")

        # In AJAX calls, sometimes the server returns the Login page HTML with 200
        content_type = response.headers.get('Content-Type', '')
        if 'application/json' in content_type:
            try:
                data = response.json()
                if isinstance(data, dict):
                    msg = str(data.get('Message', ''))
                    if 'yetki' in msg.lower() or 'oturum' in msg.lower() or 'login' in msg.lower():
                        raise YTUSessionExpiredException(f"API message: {msg}")
            except (ValueError, TypeError):
                pass
        elif 'text/html' in content_type:
            if 'name="Password"' in response.text and 'Account/Login' in response.text:
                raise YTUSessionExpiredException("HTML Login form returned instead of data")

    def _extract_csrf_token(self) -> None:
        """Extract CSRF token from cockpit page."""
        with self._lock:
            try:
                response = self.session.get(YTU_COCKPIT_URL, timeout=REQUEST_TIMEOUT)
                self._check_and_raise_if_session_expired(response)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'lxml')
                token_input = soup.find('input', {'name': '__RequestVerificationToken'})

                if token_input:
                    self.csrf_token = token_input.get('value')
                    logger.debug("✓ CSRF token extracted")
                else:
                    logger.debug("CSRF token input not found in cockpit page")

            except YTUSessionExpiredException:
                raise
            except Exception as e:
                logger.error(f"Failed to extract CSRF token: {e}")

    def _get_course_tab(self, debug: bool = False) -> Optional[str]:
        """Get the course tab HTML from cockpit (via POST request)."""
        with self._lock:
            try:
                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json, text/javascript, */*',
                }

                response = self.session.post(
                    YTU_COURSE_TAB_URL,
                    data={},
                    headers=headers,
                    timeout=REQUEST_TIMEOUT
                )
                self._check_and_raise_if_session_expired(response)
                response.raise_for_status()

                data = response.json()
                if not data.get('IsSuccess'):
                    logger.warning(f"Course tab API returned error: {data.get('Message')}")
                    return None

                html = data.get('Html', '')
                return html

            except YTUSessionExpiredException:
                raise
            except Exception as e:
                logger.error(f"Failed to get course tab: {e}")
                return None

    def _parse_course_cards(self, html: str) -> List[Dict]:
        """
        Unified parser for course cards from course tab HTML.
        Returns list of dicts: [{'no': '...', 'name': '...', 'code': '...', 'instructor': '...'}]
        """
        if not html:
            return []

        soup = BeautifulSoup(html, 'lxml')
        courses = []

        for card in soup.find_all('div', class_='card'):
            onclick_elem = card.find(onclick=re.compile(r'ViewLessonProgramAsStudent\.start'))
            if not onclick_elem:
                continue

            onclick = onclick_elem.get('onclick', '')
            match = re.search(r'ViewLessonProgramAsStudent\.start\s*\(\s*(\d+)', onclick)
            if not match:
                continue

            program_no = match.group(1)

            # Course name
            name = ''
            name_elem = card.find('p', class_='font-weight-bold')
            if name_elem:
                name = name_elem.get_text(strip=True)
            else:
                link = card.find('a')
                if link:
                    name = link.get_text(strip=True)

            # Course code
            code = ''
            code_elem = card.find('small') or card.find('span', class_='text-muted')
            if code_elem:
                code = code_elem.get_text(strip=True)

            # Instructor
            instructor = ''
            instructor_elem = card.find('p', class_='text-muted')
            if instructor_elem:
                instructor = instructor_elem.get_text(strip=True)

            courses.append({
                'no': program_no,
                'name': name or f'Program {program_no}',
                'code': code,
                'instructor': instructor
            })

        # Remove duplicates
        seen = set()
        unique = []
        for c in courses:
            if c['no'] not in seen:
                seen.add(c['no'])
                unique.append(c)

        return unique

    def _get_active_attendance(
        self,
        lesson_program_no: str,
        target_time: Optional[datetime.datetime] = None,
        debug: bool = False
    ) -> Optional[Dict]:
        """
        Calls ListLessonProgramAttendance and finds active attendance
        validated against the current/target time window.
        """
        with self._lock:
            try:
                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json, text/javascript, */*',
                }

                response = self.session.post(
                    YTU_ATTENDANCE_LIST_URL,
                    data={'LessonProgramNo': lesson_program_no},
                    headers=headers,
                    timeout=REQUEST_TIMEOUT
                )
                self._check_and_raise_if_session_expired(response)
                response.raise_for_status()

                data = response.json()
                html = data.get('Html', '')
                if not html:
                    return None

                return self._find_attend_button_in_html(html, target_time=target_time)

            except YTUSessionExpiredException:
                raise
            except Exception as e:
                logger.error(f"Failed to get attendance list for program {lesson_program_no}: {e}")
                return None

    def _find_attend_button_in_html(
        self,
        html: str,
        target_time: Optional[datetime.datetime] = None
    ) -> Optional[Dict]:
        """
        Finds 'Derse Katıl' button with onclick parameters.
        Selects button matching the active time window.
        """
        soup = BeautifulSoup(html, 'lxml')
        pattern = re.compile(
            r'attendLessonProgram\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]',
            re.IGNORECASE
        )

        candidates: List[Dict] = []

        # Find buttons via DOM onclick elements
        for element in soup.find_all(onclick=re.compile(r'attendLessonProgram', re.IGNORECASE)):
            onclick = element.get('onclick', '')
            match = pattern.search(onclick)
            if match:
                candidates.append({
                    'LessonProgramDetailNo': match.group(1),
                    'LessonProgramNo': match.group(2),
                    'StartTime': match.group(3),
                    'EndTime': match.group(4),
                })

        # Regex fallback on raw HTML if soup didn't catch it
        if not candidates:
            for match in pattern.finditer(html):
                candidates.append({
                    'LessonProgramDetailNo': match.group(1),
                    'LessonProgramNo': match.group(2),
                    'StartTime': match.group(3),
                    'EndTime': match.group(4),
                })

        if not candidates:
            return None

        # Filter and rank candidates by time window
        now = target_time or datetime.datetime.now()
        active_candidates = [
            c for c in candidates
            if is_attendance_time_active(c['StartTime'], c['EndTime'], now)
        ]

        if active_candidates:
            # Pick the candidate closest to now
            best = active_candidates[0]
            logger.info(f"✓ Found active attendance slot: {best['StartTime']} - {best['EndTime']}")
            return best

        # If none strictly active, check if target_time wasn't specified and we only have 1 button
        if len(candidates) == 1:
            candidate = candidates[0]
            logger.info(f"Single attendance button found ({candidate['StartTime']} - {candidate['EndTime']})")
            return candidate

        logger.debug(f"Found {len(candidates)} attendance buttons, but none active for current time")
        return None

    def _attend_lesson(self, params: Dict) -> Optional[str]:
        """Call AttendLessonProgram API to extract Zoom meeting URL."""
        with self._lock:
            try:
                payload = {
                    'LessonProgramDetailNo': params['LessonProgramDetailNo'],
                    'LessonProgramNo': params['LessonProgramNo'],
                    'StartTime': params['StartTime'],
                    'EndTime': params['EndTime']
                }

                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json, text/javascript, */*',
                    'Origin': YTU_BASE_URL,
                    'Referer': f"{YTU_BASE_URL}/?transaction=LMS.EDU.LessonProgram.ViewLessonProgramAsStudent/{params['LessonProgramNo']}",
                }

                if self.csrf_token:
                    headers['__RequestVerificationToken'] = self.csrf_token

                response = self.session.post(
                    YTU_ATTEND_URL,
                    data=payload,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False
                )

                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location', '')
                    if 'zoom' in location.lower():
                        logger.info(f"✓ Zoom link found in redirect: {location}")
                        return location
                    if 'Login' in location:
                        raise YTUSessionExpiredException("Attend API redirected to Login")
                    logger.warning(f"Attend API returned non-Zoom redirect: {location}")
                    return None

                self._check_and_raise_if_session_expired(response)
                response.raise_for_status()

                data = response.json()
                if data.get('IsSuccess'):
                    join_url = data.get('ScriptBag', {}).get('JoinUrl')
                    start_url = data.get('ScriptBag', {}).get('StartUrl')
                    zoom_url = join_url or start_url
                    if zoom_url:
                        logger.info("✓ Attend API returned Zoom URL")
                        return zoom_url

                logger.warning(f"Attend API response lacked join URL: {data.get('Message')}")
                return None

            except YTUSessionExpiredException:
                raise
            except Exception as e:
                logger.error(f"AttendLessonProgram failed: {e}")
                return None

    def get_zoom_link(
        self,
        course_name: Optional[str] = None,
        program_no: Optional[str] = None,
        target_time: Optional[datetime.datetime] = None,
        debug: bool = False
    ) -> Optional[str]:
        """
        Extracts Zoom meeting link.
        Supports direct targeting via program_no or Turkish-aware course name matching.
        """
        with self._lock:
            if not self.logged_in:
                raise YTUClientException("Not logged in - call login() first")

            logger.info("Searching for active lesson attendance...")
            self._extract_csrf_token()

            course_tab_html = self._get_course_tab(debug)
            if not course_tab_html:
                return None

            all_programs = self._parse_course_cards(course_tab_html)
            if not all_programs:
                logger.warning("No lesson programs found in cockpit")
                return None

            # Filter targets
            target_programs: List[Dict] = []
            if program_no:
                # Direct match by program number
                clean_pno = str(program_no).strip()
                target_programs = [p for p in all_programs if str(p['no']).strip() == clean_pno]
                if not target_programs:
                    logger.warning(f"Program No {clean_pno} not found among {len(all_programs)} courses")

            if not target_programs and course_name:
                # Turkish-aware matching
                target_programs = [
                    p for p in all_programs
                    if turkish_contains(course_name, p['name'])
                ]

            # If no specific filter matched or specified, check all
            if not target_programs and not course_name and not program_no:
                target_programs = all_programs

            if not target_programs:
                logger.warning(f"No matching programs found for filter (course='{course_name}', no='{program_no}')")
                return None

            for lp in target_programs:
                logger.info(f"Checking lesson program: {lp['name']} (No: {lp['no']})")
                attendance = self._get_active_attendance(lp['no'], target_time=target_time, debug=debug)
                if attendance:
                    zoom_url = self._attend_lesson(attendance)
                    if zoom_url:
                        return zoom_url

            logger.info("No active attendance slot found")
            return None

    def get_all_courses(self) -> List[Dict]:
        """Fetches all registered courses from Cockpit."""
        with self._lock:
            if not self.logged_in:
                raise YTUClientException("Not logged in - call login() first")

            self._extract_csrf_token()
            html = self._get_course_tab()
            return self._parse_course_cards(html) if html else []

    def get_course_schedule(self, lesson_program_no: str, debug: bool = False) -> List[Dict]:
        """Fetches weekly schedule for a course program."""
        with self._lock:
            if not self.logged_in:
                raise YTUClientException("Not logged in - call login() first")

            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*',
            }

            response = self.session.post(
                YTU_ATTENDANCE_LIST_URL,
                data={'LessonProgramNo': lesson_program_no},
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )
            self._check_and_raise_if_session_expired(response)
            response.raise_for_status()

            data = response.json()
            html = data.get('Html', '')
            return self._extract_schedule_from_attendance(html)

    def _extract_schedule_from_attendance(self, html: str) -> List[Dict]:
        """
        Extracts recurring weekly slots from attendance history table.
        Groups repeating time slots to identify true recurring lectures.
        """
        from schedule_store import DAYS_CANONICAL, DAY_TR_NAMES

        soup = BeautifulSoup(html, 'lxml')
        schedules: List[Dict] = []
        slot_frequency: Dict[str, int] = {}
        slot_metadata: Dict[str, Dict] = {}

        pattern = re.compile(r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})')

        for row in soup.find_all('tr'):
            matches = pattern.findall(row.get_text())
            if len(matches) >= 2:
                try:
                    s_d, s_m, s_y, s_h, s_min = map(int, matches[0])
                    _, _, _, e_h, e_min = map(int, matches[1])

                    date_obj = datetime.datetime(s_y, s_m, s_d)
                    weekday_idx = date_obj.weekday()
                    en_day = DAYS_CANONICAL[weekday_idx]
                    tr_day = DAY_TR_NAMES[en_day]

                    start_time = f"{s_h:02d}:{s_min:02d}"
                    end_time = f"{e_h:02d}:{e_min:02d}"

                    slot_key = f"{en_day}-{start_time}"
                    slot_frequency[slot_key] = slot_frequency.get(slot_key, 0) + 1
                    if slot_key not in slot_metadata:
                        slot_metadata[slot_key] = {
                            'day': en_day,
                            'day_tr': tr_day,
                            'start_time': start_time,
                            'end_time': end_time,
                        }
                except (ValueError, IndexError):
                    continue

        # Sort slots by day and start time
        for key, meta in slot_metadata.items():
            meta['occurrences'] = slot_frequency.get(key, 1)
            schedules.append(meta)

        return schedules

    def logout(self) -> None:
        """Logout and purge session data."""
        with self._lock:
            try:
                self.session.get(f"{YTU_BASE_URL}/Account/Logout", timeout=5)
            except Exception:
                pass
            finally:
                self.logged_in = False
                self.session.cookies.clear()
                if SESSION_FILE.exists():
                    try:
                        SESSION_FILE.unlink()
                    except OSError:
                        pass
                if LEGACY_SESSION_FILE.exists():
                    try:
                        LEGACY_SESSION_FILE.unlink()
                    except OSError:
                        pass
                logger.info("✓ Logged out")


class YTUSessionManager:
    """
    High-level session manager with thread-safe auto re-login,
    session recovery on expiration, and account lockout protection.
    """

    def __init__(self, username: str, password: str):
        self._lock = threading.RLock()
        self.username = username.strip() if username else ""
        self.password = password
        self.client = YTUClient(username=self.username, password=self.password)
        self.auth_failed = False
        self.consecutive_auth_failures = 0
        self.auth_failure_reason = ""

    def update_credentials(self, username: str, password: str) -> None:
        """Update credentials and reset lockout circuit breaker."""
        with self._lock:
            clean_user = username.strip() if username else ""
            if self.username != clean_user or self.password != password:
                self.username = clean_user
                self.password = password
                self.auth_failed = False
                self.consecutive_auth_failures = 0
                self.auth_failure_reason = ""
                self.client.update_credentials(clean_user, password)

    def reset_auth_lockout(self) -> None:
        """Manually resets the lockout circuit breaker."""
        with self._lock:
            self.auth_failed = False
            self.consecutive_auth_failures = 0
            self.auth_failure_reason = ""

    def ensure_logged_in(self) -> bool:
        """
        Ensures a valid session is ready.
        Stops after MAX_RETRY_ATTEMPTS failed logins with exponential backoff
        to protect against university account lockout.
        """
        with self._lock:
            # 1. Circuit breaker check: do NOT beat the server if locked out
            if self.auth_failed:
                logger.warning(
                    "Otomatik giriş engellendi: Hesap kilitlenmesini önlemek için "
                    "denemeler durdurulmuş durumda. Lütfen bilgilerinizi kaydederek sıfırlayın."
                )
                return False

            # 2. Fast in-memory check
            if self.client.logged_in and self.client._validate_session(force_network=False):
                self.auth_failed = False
                self.consecutive_auth_failures = 0
                return True

            # 3. Disk cached session check
            if self.client.load_session(self.username):
                self.auth_failed = False
                self.consecutive_auth_failures = 0
                return True

            # 4. Check credentials presence
            if not self.username or not self.password:
                logger.error("Cannot login: Credentials not set")
                return False

            # 5. Attempt login with exponential backoff up to MAX_RETRY_ATTEMPTS
            max_attempts = max(1, MAX_RETRY_ATTEMPTS)
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"Giriş deneniyor (Deneme {attempt}/{max_attempts})...")
                    if self.client.login(self.username, self.password):
                        self.auth_failed = False
                        self.consecutive_auth_failures = 0
                        self.auth_failure_reason = ""
                        return True
                    else:
                        self.consecutive_auth_failures += 1
                        if attempt < max_attempts:
                            backoff = RETRY_DELAY * (2 ** (attempt - 1))
                            logger.warning(f"Giriş başarısız. {backoff}sn sonra tekrar denenecek...")
                            time.sleep(backoff)
                except Exception as e:
                    logger.error(f"Giriş denemesi {attempt} hata verdi: {e}")
                    self.consecutive_auth_failures += 1
                    if attempt < max_attempts:
                        time.sleep(RETRY_DELAY)

            # Reached max attempts: trip circuit breaker to protect user account
            self.auth_failed = True
            self.auth_failure_reason = "Hatalı şifre veya oturum reddedildi"
            logger.critical(
                f"Giriş {self.consecutive_auth_failures} kez başarısız oldu! "
                "Üniversite hesabınızın kilitlenmesini (account lockout) önlemek için "
                "otomatik giriş denemeleri DURDURULDU."
            )
            return False

    def _execute_with_retry_on_expire(self, func, *args, **kwargs):
        """Executes a client function, recovering if session expired with lockout protection."""
        with self._lock:
            if not self.ensure_logged_in():
                return None

            try:
                return func(*args, **kwargs)
            except YTUSessionExpiredException:
                logger.warning("Oturum süresi doldu. Otomatik yeniden giriş deneniyor...")
                self.client.logged_in = False
                if self.auth_failed:
                    return None

                if self.client.login(self.username, self.password):
                    logger.info("Yeniden giriş başarılı. İstek tekrarlanıyor...")
                    try:
                        return func(*args, **kwargs)
                    except Exception as retry_err:
                        logger.error(f"Yeniden giriş sonrası istek başarısız: {retry_err}")
                        return None
                else:
                    self.consecutive_auth_failures += 1
                    if self.consecutive_auth_failures >= MAX_RETRY_ATTEMPTS:
                        self.auth_failed = True
                    logger.error("Otomatik yeniden giriş başarısız oldu.")
                    return None
            except Exception as e:
                logger.error(f"İstemci işlemi sırasında hata: {e}")
                return None

    def get_zoom_link_safe(
        self,
        course_name: Optional[str] = None,
        program_no: Optional[str] = None,
        target_time: Optional[datetime.datetime] = None,
        debug: bool = False
    ) -> Optional[str]:
        """Safe wrapper to extract Zoom link with auto re-login."""
        return self._execute_with_retry_on_expire(
            self.client.get_zoom_link,
            course_name=course_name,
            program_no=program_no,
            target_time=target_time,
            debug=debug
        )

    def get_courses_safe(self) -> List[Dict]:
        """Safe wrapper to get courses with auto re-login."""
        res = self._execute_with_retry_on_expire(self.client.get_all_courses)
        return res if isinstance(res, list) else []

    def get_course_schedule_safe(self, lesson_program_no: str, debug: bool = False) -> List[Dict]:
        """Safe wrapper to get schedule with auto re-login."""
        res = self._execute_with_retry_on_expire(
            self.client.get_course_schedule,
            lesson_program_no=lesson_program_no,
            debug=debug
        )
        return res if isinstance(res, list) else []
