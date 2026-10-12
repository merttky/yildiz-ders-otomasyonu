"""
YILDIZ Ders Otomasyonu - Scheduler Engine
Pure-logic background scheduler with date-keyed deduplication,
catch-up window, state-based notifications (anti-spam), and account lockout prevention.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from config import (
    CHECK_INTERVAL,
    HISTORY_FILE,
    SCHEDULER_JOIN_AFTER,
    SCHEDULER_JOIN_BEFORE,
    SCHEDULER_RETRY_INTERVAL,
)
from schedule_store import (
    DAYS_CANONICAL,
    DAY_TR_NAMES,
    get_current_day,
    load_schedule,
)

logger = logging.getLogger(__name__)


@dataclass
class JoinResult:
    """Result of an automated join attempt."""
    success: bool
    status: str = "LINK_NOT_FOUND"  # SUCCESS, LINK_NOT_FOUND, AUTH_FAILED, ERROR
    message: str = ""


class LessonScheduler:
    """
    Automated background scheduler for attending lectures on time.

    Key Features:
    - Date-keyed deduplication prevents weekly lockouts when app runs multi-week.
    - Anti-Spam Notifications: Per-state notifications instead of per-retry notifications:
        1. "Ders Saati Geldi / Link Bekleniyor" (sent once at window start)
        2. "Ders Linki Hâlâ Bekleniyor" (sent once if 15 mins passed after class start)
        3. "Derse Katılım Başarılı" (sent once when Zoom launched)
        4. "Ders Kaçırıldı" (sent once when tracking window closes without success)
        5. "Giriş Başarısız" (sent once on auth failure, circuit-breaker tripped)
    - Anti-Account-Lockout: If authentication fails, trips circuit breaker and
      halts repeated login attempts to protect university Active Directory/SSO account.
    """

    def __init__(
        self,
        join_callback: Callable[[Dict], Union[JoinResult, bool]],
        notify_waiting_callback: Optional[Callable[[str, str], None]] = None,
        notify_still_waiting_callback: Optional[Callable[[str, str, int], None]] = None,
        notify_missed_callback: Optional[Callable[[str, str, str], None]] = None,
        notify_auth_failed_callback: Optional[Callable[[str, str], None]] = None,
        on_auth_failed_alert: Optional[Callable[[], None]] = None,
        schedule_file: Optional[Path] = None,
        history_file: Optional[Path] = None,
        check_interval: int = CHECK_INTERVAL,
        join_before_sec: int = SCHEDULER_JOIN_BEFORE,
        join_after_sec: int = SCHEDULER_JOIN_AFTER,
        retry_interval_sec: int = SCHEDULER_RETRY_INTERVAL,
        still_waiting_sec: int = 15 * 60,
    ):
        self.join_callback = join_callback
        self.notify_waiting_callback = notify_waiting_callback
        self.notify_still_waiting_callback = notify_still_waiting_callback
        self.notify_missed_callback = notify_missed_callback
        self.notify_auth_failed_callback = notify_auth_failed_callback
        self.on_auth_failed_alert = on_auth_failed_alert

        self.schedule_file = schedule_file
        self.history_file = Path(history_file or HISTORY_FILE)
        self.check_interval = check_interval
        self.join_before_sec = join_before_sec
        self.join_after_sec = join_after_sec
        self.retry_interval_sec = retry_interval_sec
        self.still_waiting_sec = still_waiting_sec

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # State tracking sets (date-scoped keys)
        # Load persistent join history across app restarts
        self.joined_keys: Set[str] = self._load_history()
        self.notified_waiting_keys: Set[str] = set()
        self.notified_still_waiting_keys: Set[str] = set()
        self.notified_missed_keys: Set[str] = set()
        self.notified_auth_failed_keys: Set[str] = set()

        self.last_attempt_timestamps: Dict[str, float] = {}
        self.auth_lockout_active: bool = False
        self._is_joining: bool = False

        # Prune keys older than 7 days on startup
        self.prune_old_keys(datetime.date.today())

    def _load_history(self) -> Set[str]:
        """Loads persistently stored joined lesson keys from history_file."""
        if not self.history_file or not self.history_file.exists():
            return set()

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                keys = data.get("joined_keys", [])
            elif isinstance(data, list):
                keys = data
            else:
                keys = []
            loaded = {str(k) for k in keys if isinstance(k, (str, int))}
            logger.info(f"Loaded {len(loaded)} joined lesson history entries from {self.history_file}")
            return loaded
        except Exception as e:
            logger.warning(f"Could not load attendance history from {self.history_file}: {e}")
            return set()

    def _save_history(self) -> None:
        """
        Atomically saves joined_keys to history_file with 0600 permissions.
        Ensures attendance history survives app restarts.
        """
        if not self.history_file:
            return

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "joined_keys": sorted(list(self.joined_keys)),
                "updated_at": datetime.datetime.now().isoformat()
            }

            temp_dir = self.history_file.parent
            temp_fd, temp_path_str = tempfile.mkstemp(
                prefix="history_",
                suffix=".tmp",
                dir=temp_dir
            )
            temp_path = Path(temp_path_str)

            try:
                with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(temp_path, self.history_file)
                try:
                    os.chmod(self.history_file, 0o600)
                except OSError:
                    pass
                logger.debug(f"Attendance history saved ({len(self.joined_keys)} keys) to {self.history_file}")
            except Exception:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                raise
        except Exception as e:
            logger.error(f"Failed to save attendance history to {self.history_file}: {e}")

    def record_joined_lesson(
        self,
        lesson: Dict,
        target_dt: Optional[datetime.datetime] = None
    ) -> str:
        """
        Marks a lesson as joined (e.g. from manual join UI) and persists history.
        Prevents duplicate Zoom launches if app restarts or scheduler checks again today.
        """
        dt = target_dt or datetime.datetime.now()
        date_str = dt.strftime("%Y-%m-%d")
        day = get_current_day(dt)
        lesson_key = self.build_lesson_key(date_str, day, lesson)
        with self._lock:
            self.joined_keys.add(lesson_key)
            self._save_history()
        logger.info(f"[SCHEDULER] Recorded joined lesson: {lesson_key}")
        return lesson_key

    def build_lesson_key(self, date_str: str, day: str, lesson: Dict) -> str:
        """
        Builds date-scoped unique key for a lesson.
        Example: '2026-10-12:Monday:09:00:130766' or '2026-10-12:Monday:09:00:Matematik'
        """
        hour = lesson.get("hour", "00:00")
        prog_or_desc = lesson.get("program_no") or lesson.get("desc", "")
        return f"{date_str}:{day}:{hour}:{prog_or_desc}"

    def reset_auth_lockout(self) -> None:
        """Resets the circuit breaker when user updates credentials."""
        with self._lock:
            self.auth_lockout_active = False
            self.notified_auth_failed_keys.clear()
            logger.info("Scheduler auth circuit breaker reset.")

    def prune_old_keys(self, current_date: datetime.date) -> None:
        """Removes keys older than 7 days from in-memory sets and persists updated history."""
        with self._lock:
            cutoff = current_date - datetime.timedelta(days=7)
            cutoff_str = cutoff.strftime("%Y-%m-%d")

            def filter_set(s: Set[str]) -> Set[str]:
                return {k for k in s if k.split(":")[0] >= cutoff_str}

            prev_joined_count = len(self.joined_keys)
            self.joined_keys = filter_set(self.joined_keys)
            self.notified_waiting_keys = filter_set(self.notified_waiting_keys)
            self.notified_still_waiting_keys = filter_set(self.notified_still_waiting_keys)
            self.notified_missed_keys = filter_set(self.notified_missed_keys)
            self.notified_auth_failed_keys = filter_set(self.notified_auth_failed_keys)

            self.last_attempt_timestamps = {
                k: v for k, v in self.last_attempt_timestamps.items()
                if k.split(":")[0] >= cutoff_str
            }

            if len(self.joined_keys) != prev_joined_count:
                self._save_history()

    def is_lesson_in_window(
        self,
        lesson_hour: str,
        now: datetime.datetime
    ) -> Tuple[bool, float]:
        """
        Checks if current time is within [start - join_before, start + join_after].
        Returns (in_window, diff_seconds).
        Negative diff = before start time; Positive diff = after start time.
        """
        try:
            h, m = map(int, lesson_hour.split(":"))
            lesson_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        except Exception:
            return False, 0.0

        diff_seconds = (now - lesson_dt).total_seconds()
        in_window = (-self.join_before_sec <= diff_seconds <= self.join_after_sec)
        return in_window, diff_seconds

    def check_and_trigger(self, now: Optional[datetime.datetime] = None) -> List[Dict]:
        """
        Core evaluation method.
        Evaluates today's schedule, handles anti-spam state transitions,
        and triggers join attempts with account lockout protection.
        """
        current_dt = now or datetime.datetime.now()
        current_date_str = current_dt.strftime("%Y-%m-%d")
        current_day = get_current_day(current_dt)

        self.prune_old_keys(current_dt.date())

        schedule = load_schedule(self.schedule_file)
        today_lessons = schedule.get(current_day, [])

        triggered_lessons = []

        for lesson in today_lessons:
            hour = lesson.get("hour", "")
            course_name = lesson.get("desc", "")
            in_window, diff = self.is_lesson_in_window(hour, current_dt)
            lesson_key = self.build_lesson_key(current_date_str, current_day, lesson)

            # Check if lesson has already been successfully joined
            if lesson_key in self.joined_keys:
                continue

            # ── STATE 1: Lesson in window ─────────────────────────────────────
            if in_window:
                # 1a. Notify "Link Bekleniyor" ONCE at window start
                if lesson_key not in self.notified_waiting_keys:
                    self.notified_waiting_keys.add(lesson_key)
                    if self.notify_waiting_callback:
                        try:
                            self.notify_waiting_callback(course_name, hour)
                        except Exception as e:
                            logger.warning(f"notify_waiting_callback error: {e}")

                # 1b. Notify "Hâlâ Bekleniyor" ONCE if 15+ minutes past class start
                if diff >= self.still_waiting_sec and lesson_key not in self.notified_still_waiting_keys:
                    self.notified_still_waiting_keys.add(lesson_key)
                    if self.notify_still_waiting_callback:
                        try:
                            minutes_elapsed = int(self.still_waiting_sec // 60)
                            self.notify_still_waiting_callback(course_name, hour, minutes_elapsed)
                        except Exception as e:
                            logger.warning(f"notify_still_waiting_callback error: {e}")

                # 1c. Account Lockout Protection Check
                # If authentication previously failed, do NOT attempt network logins
                if self.auth_lockout_active:
                    continue

                # 1d. Check retry throttle
                now_ts = time.time()
                last_attempt = self.last_attempt_timestamps.get(lesson_key, 0.0)
                if (now_ts - last_attempt) < self.retry_interval_sec:
                    continue

                # 1e. Execute join attempt
                self.last_attempt_timestamps[lesson_key] = now_ts
                logger.info(
                    f"[SCHEDULER] Join attempt for: {lesson_key} "
                    f"({course_name} at {hour}, offset: {diff:+.0f}s)"
                )

                with self._lock:
                    if self._is_joining:
                        logger.debug("Another join is already running; skipping")
                        continue
                    self._is_joining = True

                try:
                    raw_res = self.join_callback(lesson)
                    if isinstance(raw_res, bool):
                        res = JoinResult(
                            success=raw_res,
                            status="SUCCESS" if raw_res else "LINK_NOT_FOUND"
                        )
                    else:
                        res = raw_res

                    # Handle outcomes
                    if res.success or res.status == "SUCCESS":
                        with self._lock:
                            self.joined_keys.add(lesson_key)
                            self._save_history()
                        logger.info(f"[SCHEDULER] ✓ Successfully joined lesson: {lesson_key}")
                        triggered_lessons.append(lesson)

                    elif res.status == "AUTH_FAILED":
                        # Authentication failed: Trip circuit breaker!
                        self.auth_lockout_active = True
                        logger.critical(
                            f"[SCHEDULER] Authentication failed for {lesson_key}. "
                            "Halting automated retries to prevent university account lockout!"
                        )
                        if lesson_key not in self.notified_auth_failed_keys:
                            self.notified_auth_failed_keys.add(lesson_key)
                            if self.notify_auth_failed_callback:
                                try:
                                    self.notify_auth_failed_callback(course_name, hour)
                                except Exception as e:
                                    logger.warning(f"notify_auth_failed_callback error: {e}")
                            if self.on_auth_failed_alert:
                                try:
                                    self.on_auth_failed_alert()
                                except Exception as e:
                                    logger.warning(f"on_auth_failed_alert error: {e}")

                    elif res.status == "LINK_NOT_FOUND":
                        # Normal waiting state: Silent retry! No spam!
                        logger.debug(
                            f"[SCHEDULER] Link not ready yet for {lesson_key}. "
                            f"Will retry silently in {self.retry_interval_sec}s."
                        )

                except Exception as err:
                    logger.error(f"[SCHEDULER] Join callback exception for {lesson_key}: {err}")
                finally:
                    with self._lock:
                        self._is_joining = False

            # ── STATE 2: Window has expired ───────────────────────────────────
            elif diff > self.join_after_sec:
                # If we previously waited for this lesson, never joined it, and haven't notified missed yet:
                if (
                    lesson_key in self.notified_waiting_keys
                    and lesson_key not in self.joined_keys
                    and lesson_key not in self.notified_missed_keys
                ):
                    self.notified_missed_keys.add(lesson_key)
                    logger.warning(f"[SCHEDULER] Tracking window expired for {lesson_key} without join.")
                    if self.notify_missed_callback:
                        try:
                            self.notify_missed_callback(
                                course_name,
                                hour,
                                "Ders süresi aşıldı, toplantı linki açılmadı"
                            )
                        except Exception as e:
                            logger.warning(f"notify_missed_callback error: {e}")

        return triggered_lessons

    def _run_loop(self) -> None:
        """Background thread execution loop."""
        logger.info(
            f"Scheduler loop started (check_interval={self.check_interval}s, "
            f"window=[-{self.join_before_sec // 60}m, +{self.join_after_sec // 60}m], "
            f"retry={self.retry_interval_sec}s)"
        )
        while not self._stop_event.is_set():
            try:
                self.check_and_trigger()
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}", exc_info=True)

            self._stop_event.wait(self.check_interval)

        logger.info("Scheduler loop terminated")

    def start(self) -> None:
        """Starts background scheduler thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                logger.warning("Scheduler thread is already running")
                return

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="SchedulerThread",
                daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stops background scheduler thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    @property
    def is_running(self) -> bool:
        """Thread-safe property checking if background scheduler is currently alive."""
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def get_next_lesson(self, now: Optional[datetime.datetime] = None) -> Optional[Dict]:
        """
        Pure function finding the currently ongoing or upcoming next lesson.
        Does not perform network calls or disk writes.
        Returns None if schedule is completely empty.

        Returned dictionary structure:
        {"day": str, "day_label": str, "hour": str, "desc": str, "delta_seconds": int, "is_now": bool}
        """
        current_dt = now or datetime.datetime.now()
        schedule = load_schedule(self.schedule_file)
        if not schedule:
            return None

        today_canonical = get_current_day(current_dt)

        def _parse_time(hour_str: str) -> Optional[Tuple[int, int]]:
            try:
                parts = hour_str.strip().split(":")
                return int(parts[0]), int(parts[1])
            except Exception:
                return None

        def _sort_key(lesson: Dict) -> Tuple[int, int]:
            t = _parse_time(lesson.get("hour", ""))
            return t if t is not None else (99, 99)

        # 1. Check today's lessons
        today_lessons = sorted(schedule.get(today_canonical, []), key=_sort_key)

        # 1a. Check for currently ongoing lesson within attendance window
        for lesson in today_lessons:
            hour = lesson.get("hour", "")
            in_win, diff = self.is_lesson_in_window(hour, current_dt)
            if in_win:
                t = _parse_time(hour)
                if t:
                    lesson_dt = current_dt.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
                    delta = int((lesson_dt - current_dt).total_seconds())
                    delta_seconds = max(0, delta)
                else:
                    delta_seconds = 0

                return {
                    "day": today_canonical,
                    "day_label": "bugün",
                    "hour": hour,
                    "desc": lesson.get("desc", ""),
                    "delta_seconds": delta_seconds,
                    "is_now": True,
                }

        # 1b. Check for upcoming lesson later today
        for lesson in today_lessons:
            hour = lesson.get("hour", "")
            t = _parse_time(hour)
            if not t:
                continue
            lesson_dt = current_dt.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
            if lesson_dt > current_dt:
                delta = int((lesson_dt - current_dt).total_seconds())
                return {
                    "day": today_canonical,
                    "day_label": "bugün",
                    "hour": hour,
                    "desc": lesson.get("desc", ""),
                    "delta_seconds": delta,
                    "is_now": False,
                }

        # 2. Today has no remaining lessons. Search following days (tomorrow and beyond)
        try:
            today_idx = DAYS_CANONICAL.index(today_canonical)
        except ValueError:
            today_idx = 0

        for offset in range(1, 8):
            next_idx = (today_idx + offset) % 7
            next_day = DAYS_CANONICAL[next_idx]
            day_lessons = sorted(schedule.get(next_day, []), key=_sort_key)
            if day_lessons:
                first_lesson = day_lessons[0]
                hour = first_lesson.get("hour", "")
                t = _parse_time(hour)
                h, m = t if t else (0, 0)

                target_date = current_dt.date() + datetime.timedelta(days=offset)
                target_dt = datetime.datetime.combine(target_date, datetime.time(h, m))
                delta = max(0, int((target_dt - current_dt).total_seconds()))

                if offset == 1:
                    day_label = "yarın"
                else:
                    day_label = DAY_TR_NAMES.get(next_day, next_day)

                return {
                    "day": next_day,
                    "day_label": day_label,
                    "hour": hour,
                    "desc": first_lesson.get("desc", ""),
                    "delta_seconds": delta,
                    "is_now": False,
                }

        return None
