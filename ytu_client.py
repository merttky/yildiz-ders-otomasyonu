"""
YILDIZ Ders Otomasyonu - LMS HTTP Client
Selenium-free API client using requests + BeautifulSoup
"""
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
import re
import logging
from typing import Optional, List, Dict
from urllib.parse import urljoin
from pathlib import Path
import pickle
import time
import ssl
import urllib3
from urllib3.util.ssl_ import create_urllib3_context

from config import (
    YTU_BASE_URL, YTU_LOGIN_URL, YTU_COCKPIT_URL,
    REQUEST_TIMEOUT, USER_AGENT, SESSION_FILE, SESSION_LIFETIME,
    SELECTORS, MAX_RETRY_ATTEMPTS, RETRY_DELAY
)

# API endpoints
YTU_ATTEND_URL = f"{YTU_BASE_URL}/ViewLessonProgramAsStudent/AttendLessonProgram"
YTU_COURSE_TAB_URL = f"{YTU_BASE_URL}/ViewCockpit/GetCourseTab"
YTU_ATTENDANCE_LIST_URL = f"{YTU_BASE_URL}/ViewLessonProgramAsStudent/ListLessonProgramAttendance"
YTU_LESSON_VIEW_URL = f"{YTU_BASE_URL}/ViewLessonProgramAsStudent/View"

# Disable SSL warnings (YTU server uses legacy SSL)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class LegacySSLAdapter(HTTPAdapter):
    """
    Custom HTTP Adapter that enables legacy SSL renegotiation.
    Required for YTU server which uses older SSL/TLS configuration.
    """
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)


class YTUClientException(Exception):
    """Custom exception for YTU Client errors"""
    pass


class YTUClient:
    """
    YTU Online LMS HTTP client

    Features:
    - JSON-based login authentication
    - Session management with cookie persistence
    - API-based Zoom link extraction (AttendLessonProgram)
    - CSRF token handling
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': USER_AGENT,
            'Accept': 'application/json, text/html, */*',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1',
        })

        # Mount legacy SSL adapter for YTU server compatibility
        self.session.mount('https://', LegacySSLAdapter())
        self.session.verify = False

        self.logged_in = False
        self.csrf_token = None  # Will be extracted from pages

    def login(self, username: str, password: str) -> bool:
        """
        Login to YTU Online using JSON-based AJAX authentication
        """
        try:
            logger.info(f"Attempting login for: {username}")

            login_payload = {
                "Username": username,
                "Password": password,
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

            success = self._verify_login(response)

            if success:
                self.logged_in = True
                logger.info("✓ Login successful")
                self._save_session()
                return True
            else:
                logger.warning("✗ Login failed - invalid credentials")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Login request failed: {e}")
            raise YTUClientException(f"Login failed: {e}")

    def _verify_login(self, response: requests.Response) -> bool:
        """Verify if login was successful"""
        if 'Login' not in response.url:
            return True

        cookie_names = [cookie.name for cookie in self.session.cookies]
        auth_cookies = ['.ASPXAUTH', 'ASP.NET_SessionId', '.AspNetCore.Session']

        if any(auth_cookie in cookie_names for auth_cookie in auth_cookies):
            return True

        try:
            json_response = response.json()
            if json_response.get('success') or json_response.get('Success'):
                return True
        except ValueError:
            pass

        if response.status_code == 200:
            return True

        return False

    def _save_session(self):
        """Save session cookies to disk"""
        try:
            session_data = {
                'cookies': self.session.cookies.get_dict(),
                'timestamp': time.time()
            }
            with open(SESSION_FILE, 'wb') as f:
                pickle.dump(session_data, f)
            logger.debug("✓ Session saved")
        except Exception as e:
            logger.warning(f"Failed to save session: {e}")

    def load_session(self) -> bool:
        """Load saved session from disk"""
        if not SESSION_FILE.exists():
            return False

        try:
            with open(SESSION_FILE, 'rb') as f:
                session_data = pickle.load(f)

            age = time.time() - session_data['timestamp']
            if age > SESSION_LIFETIME:
                logger.info(f"Session expired ({age:.0f}s)")
                return False

            for name, value in session_data['cookies'].items():
                self.session.cookies.set(name, value)

            if self._validate_session():
                self.logged_in = True
                logger.info(f"✓ Loaded session (age: {age:.0f}s)")
                return True

            return False

        except Exception as e:
            logger.warning(f"Failed to load session: {e}")
            return False

    def _validate_session(self) -> bool:
        """Check if current session is still valid"""
        try:
            response = self.session.get(
                YTU_COCKPIT_URL,
                timeout=5,
                allow_redirects=False
            )

            if response.status_code == 302:
                location = response.headers.get('Location', '')
                if 'Login' in location:
                    return False

            return response.status_code == 200

        except Exception:
            return False

    def get_zoom_link(self, course_name: Optional[str] = None, debug: bool = False) -> Optional[str]:
        """
        Get Zoom meeting link using the correct API flow:
        1. Get cockpit page to extract CSRF token
        2. Get course tab to find lesson programs
        3. For each lesson program, get attendance list
        4. Find active attendance with "Derse Katıl" button
        5. Call AttendLessonProgram API to get Zoom link

        Args:
            course_name: Optional course name to filter
            debug: If True, save HTML to files for inspection

        Returns:
            Zoom meeting URL or None
        """
        if not self.logged_in:
            raise YTUClientException("Not logged in - call login() first")

        try:
            logger.info("Searching for active classes...")

            # Step 0: Get cockpit page to extract CSRF token
            logger.info("Step 0: Getting CSRF token...")
            self._extract_csrf_token()

            if not self.csrf_token:
                logger.warning("Could not extract CSRF token")

            # Step 1: Get course tab to find lesson programs
            logger.info("Step 1: Getting course tab...")
            course_tab_html = self._get_course_tab(debug)

            if not course_tab_html:
                logger.warning("Could not get course tab")
                return None

            # Step 2: Extract lesson program numbers from course cards
            lesson_programs = self._extract_lesson_programs(course_tab_html, course_name)

            if not lesson_programs:
                logger.warning("No lesson programs found")
                return None

            logger.info(f"Found {len(lesson_programs)} lesson program(s)")

            # Step 3: For each lesson program, check for active attendance
            for lp in lesson_programs:
                logger.info(f"Checking lesson program: {lp['name']} (No: {lp['no']})")

                attendance = self._get_active_attendance(lp['no'], debug)
                if attendance:
                    logger.info(f"Found active attendance: {attendance}")

                    # Step 4: Call AttendLessonProgram API
                    zoom_url = self._attend_lesson(attendance)
                    if zoom_url:
                        return zoom_url

            logger.warning("No active attendance found in any lesson program")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get Zoom link: {e}")
            raise YTUClientException(f"Failed to get Zoom link: {e}")

    def _extract_csrf_token(self):
        """Extract CSRF token from cockpit page"""
        try:
            response = self.session.get(YTU_COCKPIT_URL, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            # Extract token from: <input name="__RequestVerificationToken" type="hidden" value="...">
            soup = BeautifulSoup(response.text, 'lxml')
            token_input = soup.find('input', {'name': '__RequestVerificationToken'})

            if token_input:
                self.csrf_token = token_input.get('value')
                logger.debug(f"CSRF token extracted: {self.csrf_token[:20]}...")
            else:
                logger.warning("CSRF token input not found in page")

        except Exception as e:
            logger.error(f"Failed to extract CSRF token: {e}")

    def _get_course_tab(self, debug: bool = False) -> Optional[str]:
        """Get the course tab HTML from cockpit (via POST request)"""
        try:
            # Set AJAX headers - MUST use POST
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*',
            }

            response = self.session.post(
                YTU_COURSE_TAB_URL,
                data={},  # Empty data for POST
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()

            # Response is JSON with HTML inside: {"IsSuccess": true, "Html": "<div>..."}
            data = response.json()

            if not data.get('IsSuccess'):
                logger.warning(f"Course tab API returned error: {data.get('Message')}")
                return None

            html = data.get('Html', '')

            if debug:
                debug_file = Path(__file__).parent / "debug_course_tab.html"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(html)
                logger.info(f"DEBUG: Saved course tab to {debug_file}")

            return html

        except ValueError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get course tab: {e}")
            return None

    def _extract_lesson_programs(self, html: str, course_name: Optional[str] = None) -> List[Dict]:
        """
        Extract lesson program numbers from course cards

        Course cards have onclick like: ViewLessonProgramAsStudent.start(12345)
        Course name is in the card footer
        """
        soup = BeautifulSoup(html, 'lxml')
        programs = []

        # Find all card containers
        cards = soup.find_all('div', class_='card')

        for card in cards:
            # Find onclick with lesson program number
            onclick_elem = card.find(onclick=re.compile(r'ViewLessonProgramAsStudent\.start'))
            if not onclick_elem:
                continue

            onclick = onclick_elem.get('onclick', '')
            match = re.search(r'ViewLessonProgramAsStudent\.start\s*\(\s*(\d+)', onclick)
            if not match:
                continue

            program_no = match.group(1)

            # Get course name from card
            name = ''
            name_elem = card.find('p', class_='font-weight-bold')
            if name_elem:
                name = name_elem.get_text(strip=True)
            else:
                # Fallback: look for any link text
                link = card.find('a')
                if link:
                    name = link.get_text(strip=True)

            # Filter by course name if specified
            if course_name and name:
                if course_name.lower() not in name.lower():
                    continue

            programs.append({
                'no': program_no,
                'name': name or f'Program {program_no}'
            })
            logger.debug(f"Found lesson program: {program_no} - {name}")

        # Remove duplicates
        seen = set()
        unique_programs = []
        for p in programs:
            if p['no'] not in seen:
                seen.add(p['no'])
                unique_programs.append(p)

        return unique_programs

    def _get_active_attendance(self, lesson_program_no: str, debug: bool = False) -> Optional[Dict]:
        """
        Get active attendance from a lesson program

        Calls /ViewLessonProgramAsStudent/ListLessonProgramAttendance
        Returns the attendance params if "Derse Katıl" button is available
        """
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
            response.raise_for_status()

            # Response is JSON with HTML inside
            data = response.json()
            html = data.get('Html', '')

            if debug:
                debug_file = Path(__file__).parent / f"debug_attendance_{lesson_program_no}.html"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(response.text)  # Save full JSON for debugging
                logger.info(f"DEBUG: Saved attendance list to {debug_file}")

            # Parse the HTML to find "Derse Katıl" buttons
            return self._find_attend_button_in_html(html)

        except ValueError as e:
            logger.error(f"Failed to parse attendance JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get attendance list: {e}")
            return None

    def _find_attend_button_in_html(self, html: str) -> Optional[Dict]:
        """
        Find "Derse Katıl" button and extract onclick parameters

        Button format:
        <a onclick="LMS.EDU.LessonProgram.ViewLessonProgramAsStudent.attendLessonProgram(124238, 118911, '23.03.2026 13:00:00', '23.03.2026 14:50:00')">Derse Katıl</a>
        """
        soup = BeautifulSoup(html, 'lxml')

        # Find all links with attendLessonProgram in onclick
        pattern = re.compile(r'attendLessonProgram\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]', re.IGNORECASE)

        for element in soup.find_all(onclick=re.compile(r'attendLessonProgram', re.IGNORECASE)):
            onclick = element.get('onclick', '')
            match = pattern.search(onclick)
            if match:
                return {
                    'LessonProgramDetailNo': match.group(1),
                    'LessonProgramNo': match.group(2),
                    'StartTime': match.group(3),
                    'EndTime': match.group(4),
                }

        # Also search the raw HTML in case BeautifulSoup missed it
        match = pattern.search(html)
        if match:
            return {
                'LessonProgramDetailNo': match.group(1),
                'LessonProgramNo': match.group(2),
                'StartTime': match.group(3),
                'EndTime': match.group(4),
            }

        logger.debug("No attend button found in HTML")
        return None

    def _find_attend_buttons(self, html: str) -> List[Dict]:
        """
        DEPRECATED - Use _find_attend_button_in_html instead
        Find all "Derse Katıl" buttons and extract onclick parameters
        """
        result = self._find_attend_button_in_html(html)
        return [result] if result else []

    def _attend_lesson(self, params: Dict) -> Optional[str]:
        """
        Call AttendLessonProgram API to get Zoom link

        API returns:
        {
            "IsSuccess": true,
            "ScriptBag": {
                "JoinUrl": "https://yildiz-edu-tr.zoom.us/w/..."
            }
        }
        """
        try:
            # PHI.UI.Transaction.call sends data as form-encoded without CSRF in data
            payload = {
                'LessonProgramDetailNo': params['LessonProgramDetailNo'],
                'LessonProgramNo': params['LessonProgramNo'],
                'StartTime': params['StartTime'],
                'EndTime': params['EndTime']
            }

            # PHI framework sends CSRF token in headers with double underscore!
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*',
                'Origin': YTU_BASE_URL,
                'Referer': f"{YTU_BASE_URL}/?transaction=LMS.EDU.LessonProgram.ViewLessonProgramAsStudent/{params['LessonProgramNo']}",
            }

            # CSRF token goes in headers with double underscore prefix
            if self.csrf_token:
                headers['__RequestVerificationToken'] = self.csrf_token

            logger.debug(f"Calling AttendLessonProgram API with: {payload}")
            logger.debug(f"Headers: {headers}")

            # Disable redirects to see the actual response
            response = self.session.post(
                YTU_ATTEND_URL,
                data=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False
            )

            logger.debug(f"Response status: {response.status_code}")
            logger.debug(f"Response headers: {dict(response.headers)}")
            logger.debug(f"Response body preview: {response.text[:500] if response.text else 'empty'}")

            # If we get a redirect, it might mean session expired or error
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('Location', '')
                logger.warning(f"API returned redirect to: {location}")

                # Check if redirect contains Zoom URL directly
                if 'zoom' in location.lower():
                    logger.info(f"✓ Zoom link found in redirect: {location}")
                    return location

                # Otherwise, it's probably an error
                logger.error("Redirect does not contain Zoom URL - session might be invalid")
                return None

            response.raise_for_status()

            # Parse JSON response
            data = response.json()
            logger.debug(f"API response: {data}")

            if data.get('IsSuccess'):
                # Student version uses JoinUrl, Instructor uses StartUrl
                join_url = data.get('ScriptBag', {}).get('JoinUrl')
                start_url = data.get('ScriptBag', {}).get('StartUrl')
                zoom_url = join_url or start_url

                if zoom_url:
                    logger.info(f"✓ API returned Zoom link")
                    return zoom_url
                else:
                    logger.warning("API success but no JoinUrl/StartUrl in response")
                    logger.debug(f"ScriptBag: {data.get('ScriptBag')}")
            else:
                message = data.get('Message', 'Unknown error')
                logger.warning(f"API returned error: {message}")

            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"AttendLessonProgram API request failed: {e}")
            return None
        except ValueError as e:
            logger.error(f"Failed to parse API response: {e}")
            return None

    def get_all_courses(self) -> List[Dict]:
        """
        Tüm kayıtlı dersleri GetCourseTab API'sinden çeker

        Returns:
            List of dicts: [
                {
                    'no': '12345',           # LessonProgramNo
                    'name': 'Matematik 1',   # Ders adı
                    'code': '',              # Ders kodu (varsa)
                    'instructor': ''         # Öğretim üyesi (varsa)
                },
                ...
            ]
        """
        if not self.logged_in:
            raise YTUClientException("Not logged in - call login() first")

        try:
            # CSRF token al
            self._extract_csrf_token()

            # Course tab HTML'ini çek
            course_tab_html = self._get_course_tab()

            if not course_tab_html:
                return []

            # Genişletilmiş extraction
            return self._extract_courses_detailed(course_tab_html)

        except Exception as e:
            logger.error(f"Failed to get courses: {e}")
            return []

    def _extract_courses_detailed(self, html: str) -> List[Dict]:
        """
        Course tab HTML'inden detaylı ders bilgilerini çıkarır
        """
        soup = BeautifulSoup(html, 'lxml')
        courses = []

        cards = soup.find_all('div', class_='card')

        for card in cards:
            # LessonProgramNo çıkart
            onclick_elem = card.find(onclick=re.compile(r'ViewLessonProgramAsStudent\.start'))
            if not onclick_elem:
                continue

            onclick = onclick_elem.get('onclick', '')
            match = re.search(r'ViewLessonProgramAsStudent\.start\s*\(\s*(\d+)', onclick)
            if not match:
                continue

            program_no = match.group(1)

            # Ders adı
            name = ''
            name_elem = card.find('p', class_='font-weight-bold')
            if name_elem:
                name = name_elem.get_text(strip=True)

            # Ders kodu (genellikle küçük font'ta)
            code = ''
            code_elem = card.find('small') or card.find('span', class_='text-muted')
            if code_elem:
                code = code_elem.get_text(strip=True)

            # Öğretim üyesi
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

        # Duplicate'leri kaldır
        seen = set()
        unique = []
        for c in courses:
            if c['no'] not in seen:
                seen.add(c['no'])
                unique.append(c)

        return unique

    def get_course_schedule(self, lesson_program_no: str, debug: bool = False) -> List[Dict]:
        """
        Ders detay sayfasından haftalık ders programını çeker

        ListLessonProgramAttendance API'sinden tarihleri alıp
        gün adına çevirir ve haftalık programı oluşturur.

        Args:
            lesson_program_no: Ders program numarası
            debug: Debug modda HTML'i dosyaya kaydet

        Returns:
            List of dicts: [
                {
                    'day': 'Monday',      # İngilizce gün adı
                    'day_tr': 'Pazartesi', # Türkçe gün adı
                    'start_time': '09:00',
                    'end_time': '10:50'
                },
                ...
            ]
        """
        if not self.logged_in:
            raise YTUClientException("Not logged in - call login() first")

        try:
            logger.info(f"Getting schedule for lesson program: {lesson_program_no}")

            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*',
            }

            # ListLessonProgramAttendance API'sini kullan
            response = self.session.post(
                YTU_ATTENDANCE_LIST_URL,
                data={'LessonProgramNo': lesson_program_no},
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()

            data = response.json()
            html = data.get('Html', '')

            if debug:
                debug_file = Path(__file__).parent / f"debug_attendance_{lesson_program_no}.html"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(html)
                logger.info(f"DEBUG: Saved attendance to {debug_file}")

            return self._extract_schedule_from_attendance(html)

        except ValueError as e:
            logger.error(f"Failed to parse attendance JSON: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to get course schedule: {e}")
            return []

    def _extract_schedule_from_attendance(self, html: str) -> List[Dict]:
        """
        Attendance HTML'inden haftalık ders programını çıkarır

        Format: dd.mm.yyyy HH:MM - dd.mm.yyyy HH:MM
        Örnek: 23.02.2026 13:00 - 23.02.2026 14:50
        """
        import datetime as dt

        # İngilizce gün numarası -> Türkçe/İngilizce eşlemesi
        day_names = {
            0: ('Monday', 'Pazartesi'),
            1: ('Tuesday', 'Salı'),
            2: ('Wednesday', 'Çarşamba'),
            3: ('Thursday', 'Perşembe'),
            4: ('Friday', 'Cuma'),
            5: ('Saturday', 'Cumartesi'),
            6: ('Sunday', 'Pazar'),
        }

        soup = BeautifulSoup(html, 'lxml')
        schedules = []
        seen_slots = set()  # Tekrarları önle

        # Tarih-saat pattern: dd.mm.yyyy HH:MM
        pattern = re.compile(r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})')

        # Tablo satırlarını tara
        for row in soup.find_all('tr'):
            text = row.get_text()

            # Başlangıç ve bitiş saatlerini bul
            matches = pattern.findall(text)

            if len(matches) >= 2:
                # İlk match: başlangıç, ikinci match: bitiş
                start_match = matches[0]
                end_match = matches[1]

                try:
                    # Tarihi parse et
                    day = int(start_match[0])
                    month = int(start_match[1])
                    year = int(start_match[2])
                    start_hour = int(start_match[3])
                    start_min = int(start_match[4])

                    end_hour = int(end_match[3])
                    end_min = int(end_match[4])

                    # datetime objesi oluştur ve gün adını al
                    date_obj = dt.datetime(year, month, day)
                    weekday = date_obj.weekday()  # 0=Monday, 6=Sunday

                    en_day, tr_day = day_names[weekday]

                    # Saat formatla
                    start_time = f"{start_hour:02d}:{start_min:02d}"
                    end_time = f"{end_hour:02d}:{end_min:02d}"

                    # Tekrar kontrolü (aynı gün-saat kombinasyonu)
                    slot_key = f"{en_day}-{start_time}"
                    if slot_key not in seen_slots:
                        seen_slots.add(slot_key)
                        schedules.append({
                            'day': en_day,
                            'day_tr': tr_day,
                            'start_time': start_time,
                            'end_time': end_time
                        })
                        logger.debug(f"Found schedule: {en_day} {start_time}-{end_time}")

                except (ValueError, IndexError) as e:
                    logger.debug(f"Could not parse date: {e}")
                    continue

        logger.info(f"Found {len(schedules)} unique schedule slots")
        return schedules

    def logout(self):
        """Logout and clear session"""
        try:
            logout_url = f"{YTU_BASE_URL}/Account/Logout"
            self.session.get(logout_url, timeout=5)
        except:
            pass
        finally:
            self.logged_in = False
            self.session.cookies.clear()
            if SESSION_FILE.exists():
                SESSION_FILE.unlink()
            logger.info("✓ Logged out")


class YTUSessionManager:
    """High-level session manager"""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.client = YTUClient()

    def ensure_logged_in(self) -> bool:
        """Ensure we have a valid session"""
        if self.client.load_session():
            logger.info("Using cached session")
            return True

        logger.info("No valid session, logging in...")
        try:
            return self.client.login(self.username, self.password)
        except YTUClientException as e:
            logger.error(f"Login failed: {e}")
            return False

    def get_zoom_link_safe(self, course_name: Optional[str] = None, debug: bool = False) -> Optional[str]:
        """Get Zoom link with automatic login if needed"""
        if not self.ensure_logged_in():
            logger.error("Cannot get Zoom link - login failed")
            return None

        try:
            return self.client.get_zoom_link(course_name, debug=debug)
        except YTUClientException as e:
            logger.error(f"Failed to get Zoom link: {e}")
            return None

    def get_courses_safe(self) -> List[Dict]:
        """Tüm dersleri güvenli şekilde çeker (login kontrollü)"""
        if not self.ensure_logged_in():
            logger.error("Cannot get courses - login failed")
            return []

        try:
            return self.client.get_all_courses()
        except YTUClientException as e:
            logger.error(f"Failed to get courses: {e}")
            return []

    def get_course_schedule_safe(self, lesson_program_no: str, debug: bool = False) -> List[Dict]:
        """Ders programını güvenli şekilde çeker (login kontrollü)"""
        if not self.ensure_logged_in():
            logger.error("Cannot get schedule - login failed")
            return []

        try:
            return self.client.get_course_schedule(lesson_program_no, debug=debug)
        except YTUClientException as e:
            logger.error(f"Failed to get schedule: {e}")
            return []
