import datetime
import tempfile
import unittest
from pathlib import Path

from schedule_store import save_schedule
from scheduler import JoinResult, LessonScheduler


class TestLessonScheduler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_schedule_file = Path(self.temp_dir.name) / "schedule.json"
        self.test_history_file = Path(self.temp_dir.name) / "test_history.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_lesson_key_includes_date(self):
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            history_file=self.test_history_file
        )
        lesson = {"hour": "09:00", "desc": "Matematik 1", "program_no": "130766"}

        key_week1 = scheduler.build_lesson_key("2026-10-12", "Monday", lesson)
        key_week2 = scheduler.build_lesson_key("2026-10-19", "Monday", lesson)

        # Different dates produce different keys, preventing multi-week lockout
        self.assertEqual(key_week1, "2026-10-12:Monday:09:00:130766")
        self.assertEqual(key_week2, "2026-10-19:Monday:09:00:130766")
        self.assertNotEqual(key_week1, key_week2)

    def test_window_detection(self):
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            history_file=self.test_history_file,
            join_before_sec=300,  # 5 min before
            join_after_sec=2700   # 45 min after
        )

        # Exact time 09:00
        now_exact = datetime.datetime(2026, 10, 12, 9, 0)
        in_win, diff = scheduler.is_lesson_in_window("09:00", now_exact)
        self.assertTrue(in_win)
        self.assertEqual(diff, 0.0)

        # 4 minutes before (08:56) -> in window
        now_before = datetime.datetime(2026, 10, 12, 8, 56)
        in_win, diff = scheduler.is_lesson_in_window("09:00", now_before)
        self.assertTrue(in_win)
        self.assertEqual(diff, -240.0)

        # 6 minutes before (08:54) -> outside window
        now_too_early = datetime.datetime(2026, 10, 12, 8, 54)
        in_win, _ = scheduler.is_lesson_in_window("09:00", now_too_early)
        self.assertFalse(in_win)

        # 30 minutes after (09:30) (e.g. computer woke up from sleep or teacher started late) -> in window
        now_wake = datetime.datetime(2026, 10, 12, 9, 30)
        in_win, diff = scheduler.is_lesson_in_window("09:00", now_wake)
        self.assertTrue(in_win)
        self.assertEqual(diff, 1800.0)

        # 50 minutes after (09:50) -> outside window
        now_too_late = datetime.datetime(2026, 10, 12, 9, 50)
        in_win, _ = scheduler.is_lesson_in_window("09:00", now_too_late)
        self.assertFalse(in_win)

    def test_retry_until_link_found(self):
        save_schedule(
            {"Monday": [{"hour": "09:00", "desc": "Matematik 1", "program_no": "123"}]},
            self.test_schedule_file
        )

        attempts = 0
        link_available = False

        def mock_join_callback(lesson):
            nonlocal attempts, link_available
            attempts += 1
            return JoinResult(
                success=link_available,
                status="SUCCESS" if link_available else "LINK_NOT_FOUND"
            )

        scheduler = LessonScheduler(
            join_callback=mock_join_callback,
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
            retry_interval_sec=10
        )

        now = datetime.datetime(2026, 10, 12, 9, 2)  # Monday 09:02

        # Cycle 1: Teacher hasn't started meeting yet (link_available = False)
        triggered = scheduler.check_and_trigger(now)
        self.assertEqual(attempts, 1)
        self.assertEqual(len(triggered), 0)
        self.assertEqual(len(scheduler.joined_keys), 0)

        # Cycle 2 immediately (within 10s retry interval): should NOT attempt again yet
        triggered = scheduler.check_and_trigger(now)
        self.assertEqual(attempts, 1)

        # Cycle 3: 15 seconds later (past retry interval), teacher started meeting!
        link_available = True
        scheduler.last_attempt_timestamps.clear()  # simulate time advance
        triggered = scheduler.check_and_trigger(now)
        self.assertEqual(attempts, 2)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(len(scheduler.joined_keys), 1)

        # Cycle 4: Already joined -> should not trigger again
        scheduler.last_attempt_timestamps.clear()
        triggered = scheduler.check_and_trigger(now)
        self.assertEqual(attempts, 2)
        self.assertEqual(len(triggered), 0)

    def test_anti_spam_notifications(self):
        """Verifies that 116 retries do NOT send 116 Discord notifications."""
        save_schedule(
            {"Monday": [{"hour": "09:00", "desc": "Fizik 1", "program_no": "999"}]},
            self.test_schedule_file
        )

        waiting_notifs = []
        still_waiting_notifs = []
        missed_notifs = []

        def mock_join_callback(lesson):
            # Meeting never opens
            return JoinResult(success=False, status="LINK_NOT_FOUND")

        scheduler = LessonScheduler(
            join_callback=mock_join_callback,
            notify_waiting_callback=lambda c, h: waiting_notifs.append((c, h)),
            notify_still_waiting_callback=lambda c, h, m: still_waiting_notifs.append((c, h, m)),
            notify_missed_callback=lambda c, h, r: missed_notifs.append((c, h, r)),
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
            retry_interval_sec=0,  # allow immediate retries for test loop
            join_before_sec=300,
            join_after_sec=2700,
            still_waiting_sec=900,
        )

        # 1. Start of window (08:58) -> exactly 1 waiting notification
        scheduler.check_and_trigger(datetime.datetime(2026, 10, 12, 8, 58))
        self.assertEqual(len(waiting_notifs), 1)

        # 2. Simulate 50 retries between 08:59 and 09:14 -> NO new notifications
        for i in range(50):
            scheduler.last_attempt_timestamps.clear()
            scheduler.check_and_trigger(datetime.datetime(2026, 10, 12, 9, 5))

        self.assertEqual(len(waiting_notifs), 1)
        self.assertEqual(len(still_waiting_notifs), 0)
        self.assertEqual(len(missed_notifs), 0)

        # 3. At 09:16 (16 mins in) -> exactly 1 still_waiting notification
        scheduler.last_attempt_timestamps.clear()
        scheduler.check_and_trigger(datetime.datetime(2026, 10, 12, 9, 16))
        self.assertEqual(len(still_waiting_notifs), 1)

        # 4. Another 50 retries between 09:17 and 09:44 -> NO new notifications
        for i in range(50):
            scheduler.last_attempt_timestamps.clear()
            scheduler.check_and_trigger(datetime.datetime(2026, 10, 12, 9, 30))

        self.assertEqual(len(still_waiting_notifs), 1)

        # 5. At 09:46 (window expired) -> exactly 1 missed notification
        scheduler.check_and_trigger(datetime.datetime(2026, 10, 12, 9, 46))
        self.assertEqual(len(missed_notifs), 1)

        # Verify TOTAL notifications over 100+ retries is exactly 3 (NOT 100+!)
        total_notifs = len(waiting_notifs) + len(still_waiting_notifs) + len(missed_notifs)
        self.assertEqual(total_notifs, 3)

    def test_account_lockout_prevention(self):
        """Verifies that bad auth trips circuit breaker and halts repeated logins."""
        save_schedule(
            {"Monday": [{"hour": "09:00", "desc": "Kimya 1", "program_no": "888"}]},
            self.test_schedule_file
        )

        attempts = 0
        auth_failed_notifs = []

        def mock_join_callback(lesson):
            nonlocal attempts
            attempts += 1
            return JoinResult(success=False, status="AUTH_FAILED", message="Bad password")

        scheduler = LessonScheduler(
            join_callback=mock_join_callback,
            notify_auth_failed_callback=lambda c, h: auth_failed_notifs.append((c, h)),
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
            retry_interval_sec=0
        )

        now = datetime.datetime(2026, 10, 12, 9, 5)

        # Cycle 1: Auth fails
        scheduler.check_and_trigger(now)
        self.assertEqual(attempts, 1)
        self.assertTrue(scheduler.auth_lockout_active)
        self.assertEqual(len(auth_failed_notifs), 1)

        # Cycles 2-10: Because auth_lockout_active is True, join_callback is NOT called!
        for _ in range(9):
            scheduler.last_attempt_timestamps.clear()
            scheduler.check_and_trigger(now)

        # Attempts should REMAIN 1! University account is protected from lockout!
        self.assertEqual(attempts, 1)
        self.assertEqual(len(auth_failed_notifs), 1)

        # When credentials are updated and lockout reset:
        scheduler.reset_auth_lockout()
        self.assertFalse(scheduler.auth_lockout_active)
        scheduler.last_attempt_timestamps.clear()

        # Next cycle attempts join again
        scheduler.check_and_trigger(now)
        self.assertEqual(attempts, 2)

    def test_pruning_old_keys(self):
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            history_file=self.test_history_file
        )
        scheduler.joined_keys.add("2026-10-01:Monday:09:00:123")  # Old (>7 days)
        scheduler.joined_keys.add("2026-10-12:Monday:09:00:123")  # Recent

        today = datetime.date(2026, 10, 12)
        scheduler.prune_old_keys(today)

        self.assertNotIn("2026-10-01:Monday:09:00:123", scheduler.joined_keys)
        self.assertIn("2026-10-12:Monday:09:00:123", scheduler.joined_keys)

    def test_history_persistence_across_restart(self):
        """Verifies that joined lessons are persisted to disk and loaded across process restarts."""
        history_file = Path(self.temp_dir.name) / "join_history.json"
        save_schedule(
            {"Monday": [{"hour": "09:00", "desc": "Matematik 1", "program_no": "130766"}]},
            self.test_schedule_file
        )

        join_calls_process_1 = 0

        def join_cb_1(lesson):
            nonlocal join_calls_process_1
            join_calls_process_1 += 1
            return JoinResult(success=True, status="SUCCESS")

        # Process 1 runs: joins the class
        scheduler_1 = LessonScheduler(
            join_callback=join_cb_1,
            schedule_file=self.test_schedule_file,
            history_file=history_file,
        )
        now = datetime.datetime(2026, 10, 12, 9, 15)  # Monday 09:15 during class
        triggered = scheduler_1.check_and_trigger(now)

        self.assertEqual(len(triggered), 1)
        self.assertEqual(join_calls_process_1, 1)
        self.assertTrue(history_file.exists())

        # Process 2 starts (simulating app restart during class)
        join_calls_process_2 = 0

        def join_cb_2(lesson):
            nonlocal join_calls_process_2
            join_calls_process_2 += 1
            return JoinResult(success=True, status="SUCCESS")

        scheduler_2 = LessonScheduler(
            join_callback=join_cb_2,
            schedule_file=self.test_schedule_file,
            history_file=history_file,
        )

        # Scheduler 2 must have loaded joined_keys from file on startup!
        expected_key = "2026-10-12:Monday:09:00:130766"
        self.assertIn(expected_key, scheduler_2.joined_keys)

        # Check and trigger again at 09:16: MUST NOT call join_cb_2! (No duplicate Zoom window!)
        now_later = datetime.datetime(2026, 10, 12, 9, 16)
        triggered_again = scheduler_2.check_and_trigger(now_later)

        self.assertEqual(len(triggered_again), 0)
        self.assertEqual(join_calls_process_2, 0)

    def test_record_joined_lesson_prevents_duplicate_join(self):
        """Verifies that manually joining a lesson persists and prevents automated scheduler re-join."""
        history_file = Path(self.temp_dir.name) / "manual_history.json"
        save_schedule(
            {"Monday": [{"hour": "10:00", "desc": "Fizik 1", "program_no": "111222"}]},
            self.test_schedule_file
        )

        join_calls = 0

        def join_cb(lesson):
            nonlocal join_calls
            join_calls += 1
            return True

        scheduler = LessonScheduler(
            join_callback=join_cb,
            schedule_file=self.test_schedule_file,
            history_file=history_file,
        )

        # Student manually clicked 'Şimdi Derse Gir' at 10:05
        now = datetime.datetime(2026, 10, 12, 10, 5)
        scheduler.record_joined_lesson(
            {"desc": "Fizik 1", "program_no": "111222", "hour": "10:00"},
            target_dt=now
        )

        # When scheduler cycle runs at 10:06, it should skip this lesson
        triggered = scheduler.check_and_trigger(now)
        self.assertEqual(len(triggered), 0)
        self.assertEqual(join_calls, 0)

    def test_get_next_lesson_ongoing(self):
        """Verifies get_next_lesson detects an active lesson in attendance window with is_now=True."""
        save_schedule(
            {"Monday": [{"hour": "09:00", "desc": "Matematik 1", "program_no": "101"}]},
            self.test_schedule_file
        )
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
        )
        # Monday 09:10 (within 09:00 - 09:45 attendance window)
        now = datetime.datetime(2026, 10, 12, 9, 10)
        next_lesson = scheduler.get_next_lesson(now)

        self.assertIsNotNone(next_lesson)
        self.assertTrue(next_lesson["is_now"])
        self.assertEqual(next_lesson["desc"], "Matematik 1")
        self.assertEqual(next_lesson["hour"], "09:00")
        self.assertEqual(next_lesson["day"], "Monday")
        self.assertEqual(next_lesson["day_label"], "bugün")

    def test_get_next_lesson_today_upcoming(self):
        """Verifies get_next_lesson picks the next upcoming lesson today with is_now=False."""
        save_schedule(
            {
                "Monday": [
                    {"hour": "10:00", "desc": "Fizik 1", "program_no": "102"},
                    {"hour": "14:00", "desc": "Kimya 1", "program_no": "103"},
                ]
            },
            self.test_schedule_file
        )
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
        )
        # Monday 08:30 (before window of 10:00 class)
        now = datetime.datetime(2026, 10, 12, 8, 30)
        next_lesson = scheduler.get_next_lesson(now)

        self.assertIsNotNone(next_lesson)
        self.assertFalse(next_lesson["is_now"])
        self.assertEqual(next_lesson["desc"], "Fizik 1")
        self.assertEqual(next_lesson["hour"], "10:00")
        self.assertEqual(next_lesson["day_label"], "bugün")
        self.assertEqual(next_lesson["delta_seconds"], 90 * 60)  # 5400s

    def test_get_next_lesson_tomorrow(self):
        """Verifies get_next_lesson picks tomorrow's first lesson with day_label='yarın' when today is done."""
        save_schedule(
            {
                "Monday": [{"hour": "09:00", "desc": "Matematik 1"}],
                "Tuesday": [{"hour": "09:00", "desc": "Lineer Cebir"}],
            },
            self.test_schedule_file
        )
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
        )
        # Monday 23:59: Monday classes finished
        now = datetime.datetime(2026, 10, 12, 23, 59)
        next_lesson = scheduler.get_next_lesson(now)

        self.assertIsNotNone(next_lesson)
        self.assertFalse(next_lesson["is_now"])
        self.assertEqual(next_lesson["day"], "Tuesday")
        self.assertEqual(next_lesson["day_label"], "yarın")
        self.assertEqual(next_lesson["desc"], "Lineer Cebir")
        self.assertEqual(next_lesson["hour"], "09:00")
        # 9 hours 1 minute = 32460s
        self.assertEqual(next_lesson["delta_seconds"], 32460)

    def test_get_next_lesson_later_in_week(self):
        """Verifies get_next_lesson finds the first upcoming day beyond tomorrow (e.g. Cuma)."""
        save_schedule(
            {"Friday": [{"hour": "11:00", "desc": "Tarih 1"}]},
            self.test_schedule_file
        )
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
        )
        # Monday 12:00 -> Next class is Friday
        now = datetime.datetime(2026, 10, 12, 12, 0)
        next_lesson = scheduler.get_next_lesson(now)

        self.assertIsNotNone(next_lesson)
        self.assertFalse(next_lesson["is_now"])
        self.assertEqual(next_lesson["day"], "Friday")
        self.assertEqual(next_lesson["day_label"], "Cuma")
        self.assertEqual(next_lesson["desc"], "Tarih 1")
        self.assertEqual(next_lesson["hour"], "11:00")

    def test_get_next_lesson_empty_schedule(self):
        """Verifies get_next_lesson returns None when schedule is empty."""
        save_schedule({}, self.test_schedule_file)
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
        )
        self.assertIsNone(scheduler.get_next_lesson(datetime.datetime.now()))

    def test_is_running_property(self):
        """Verifies scheduler is_running correctly reflects thread lifecycle."""
        scheduler = LessonScheduler(
            join_callback=lambda x: True,
            schedule_file=self.test_schedule_file,
            history_file=self.test_history_file,
        )
        self.assertFalse(scheduler.is_running)
        scheduler.start()
        self.assertTrue(scheduler.is_running)
        scheduler.stop()
        self.assertFalse(scheduler.is_running)

    def test_format_delta_time(self):
        """Verifies format_delta_time produces human-readable Turkish strings."""
        from main import format_delta_time
        self.assertEqual(format_delta_time(45), "45 sn")
        self.assertEqual(format_delta_time(720), "12 dk")
        self.assertEqual(format_delta_time(3900), "1 sa 05 dk")
        self.assertEqual(format_delta_time(3600), "1 sa 00 dk")


if __name__ == "__main__":
    unittest.main()

