import datetime
import unittest
from unittest.mock import MagicMock
import requests

from ytu_client import (
    YTUClient,
    YTUSessionExpiredException,
    is_attendance_time_active,
    parse_ytu_datetime,
)


class TestYTUClient(unittest.TestCase):
    def setUp(self):
        self.client = YTUClient()

    def test_verify_login_false_positive_prevention(self):
        """Ensure login verification relies on ground-truth Cockpit access without fake cookie whitelists."""
        # Scenario E: 200 OK with explicit failure JSON -> False immediately
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 200
        mock_resp_fail.url = "https://online.yildiz.edu.tr/Account/Login"
        mock_resp_fail.history = []
        mock_resp_fail.json.return_value = {"Success": False, "Message": "Hatalı şifre"}
        self.assertFalse(self.client._verify_login(mock_resp_fail))

        # Scenario D: Empty cookies, no JSON flag, Cockpit redirects to Login (302) -> False
        mock_resp_no_json = MagicMock()
        mock_resp_no_json.status_code = 200
        mock_resp_no_json.url = "https://online.yildiz.edu.tr/Account/Login"
        mock_resp_no_json.history = []
        mock_resp_no_json.text = ""
        mock_resp_no_json.json.side_effect = ValueError("No JSON")
        self.client.session.cookies.clear()
        self.client._validate_session = MagicMock(return_value=False)
        self.assertFalse(self.client._verify_login(mock_resp_no_json))

        # Scenario C: Fake cookie, Cockpit probe redirects to Login -> False
        self.client.session.cookies.set("FakeAuth", "invalid_value")
        self.client._validate_session = MagicMock(return_value=False)
        self.assertFalse(self.client._verify_login(mock_resp_no_json))

        # Scenario A: Real PHI LMS cookie (philms), NO JSON flag, Cockpit access verified -> True!
        self.client.session.cookies.clear()
        self.client.session.cookies.set("philms", "real_phi_session_token")
        self.client._validate_session = MagicMock(return_value=True)
        self.assertTrue(self.client._verify_login(mock_resp_no_json))

        # Scenario B: JSON Success: True, Cockpit access verified -> True
        mock_resp_success = MagicMock()
        mock_resp_success.status_code = 200
        mock_resp_success.url = "https://online.yildiz.edu.tr/Account/Login"
        mock_resp_success.history = []
        mock_resp_success.json.return_value = {"Success": True}
        self.client._validate_session = MagicMock(return_value=True)
        self.assertTrue(self.client._verify_login(mock_resp_success))

    def test_session_expired_detection(self):
        """K7 Fix: Detect session drops."""
        # HTTP 401
        resp_401 = MagicMock()
        resp_401.status_code = 401
        with self.assertRaises(YTUSessionExpiredException):
            self.client._check_and_raise_if_session_expired(resp_401)

        # Redirect to Account/Login
        resp_302 = MagicMock()
        resp_302.status_code = 302
        resp_302.headers = {"Location": "https://online.yildiz.edu.tr/Account/Login"}
        with self.assertRaises(YTUSessionExpiredException):
            self.client._check_and_raise_if_session_expired(resp_302)

        # JSON indicating session expired
        resp_json = MagicMock()
        resp_json.status_code = 200
        resp_json.headers = {"Content-Type": "application/json"}
        resp_json.json.return_value = {"Message": "Oturum süreniz doldu, lütfen tekrar giriş yapın."}
        with self.assertRaises(YTUSessionExpiredException):
            self.client._check_and_raise_if_session_expired(resp_json)

    def test_attendance_time_active_window(self):
        start = "23.03.2026 13:00:00"
        end = "23.03.2026 14:50:00"

        # 12:44 (16 min before) -> False
        self.assertFalse(is_attendance_time_active(start, end, datetime.datetime(2026, 3, 23, 12, 44)))
        # 12:50 (10 min before) -> True
        self.assertTrue(is_attendance_time_active(start, end, datetime.datetime(2026, 3, 23, 12, 50)))
        # 13:30 (during class) -> True
        self.assertTrue(is_attendance_time_active(start, end, datetime.datetime(2026, 3, 23, 13, 30)))
        # 15:00 (10 min after end) -> True (within 15 min buffer)
        self.assertTrue(is_attendance_time_active(start, end, datetime.datetime(2026, 3, 23, 15, 0)))
        # 15:15 (25 min after end) -> False
        self.assertFalse(is_attendance_time_active(start, end, datetime.datetime(2026, 3, 23, 15, 15)))

    def test_parse_course_cards(self):
        sample_html = """
        <div class="card">
            <a onclick="ViewLessonProgramAsStudent.start(130766)">Matematik 1</a>
            <p class="font-weight-bold">Matematik 1</p>
            <small>MAT1071</small>
            <p class="text-muted">Prof. Dr. Ahmet Yılmaz</p>
        </div>
        <div class="card">
            <a onclick="ViewLessonProgramAsStudent.start(126092)">İş Sağlığı</a>
            <p class="font-weight-bold">İş Sağlığı ve Güvenliği 1</p>
            <small>ISG1001</small>
        </div>
        """
        cards = self.client._parse_course_cards(sample_html)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["no"], "130766")
        self.assertEqual(cards[0]["name"], "Matematik 1")
        self.assertEqual(cards[0]["code"], "MAT1071")
        self.assertEqual(cards[0]["instructor"], "Prof. Dr. Ahmet Yılmaz")
        self.assertEqual(cards[1]["no"], "126092")

    def test_find_attend_button(self):
        sample_html = """
        <a onclick="LMS.EDU.LessonProgram.ViewLessonProgramAsStudent.attendLessonProgram(124238, 118911, '23.03.2026 13:00:00', '23.03.2026 14:50:00')">Derse Katıl</a>
        """
        now = datetime.datetime(2026, 3, 23, 13, 15)
        btn = self.client._find_attend_button_in_html(sample_html, target_time=now)
        self.assertIsNotNone(btn)
        self.assertEqual(btn["LessonProgramDetailNo"], "124238")
        self.assertEqual(btn["LessonProgramNo"], "118911")
        self.assertEqual(btn["StartTime"], "23.03.2026 13:00:00")
        self.assertEqual(btn["EndTime"], "23.03.2026 14:50:00")

    def test_session_manager_account_lockout_circuit_breaker(self):
        """Verifies YTUSessionManager stops retrying login after MAX_RETRY_ATTEMPTS to prevent lockout."""
        from unittest.mock import patch
        from ytu_client import YTUSessionManager

        manager = YTUSessionManager("ogrenci@std.yildiz.edu.tr", "hatali_sifre")
        manager.client.load_session = MagicMock(return_value=False)
        manager.client._validate_session = MagicMock(return_value=False)

        login_mock = MagicMock(return_value=False)
        manager.client.login = login_mock

        with patch("time.sleep", return_value=None):
            # First ensure_logged_in: tries 3 times with backoff, then trips circuit breaker
            res1 = manager.ensure_logged_in()
            self.assertFalse(res1)
            self.assertEqual(login_mock.call_count, 3)
            self.assertTrue(manager.auth_failed)

            # Subsequent call: Circuit breaker is tripped! Must NOT call login again!
            res2 = manager.ensure_logged_in()
            self.assertFalse(res2)
            self.assertEqual(login_mock.call_count, 3)  # Still 3, zero additional calls!

            # When user updates credentials:
            manager.update_credentials("ogrenci@std.yildiz.edu.tr", "yeni_dogru_sifre")
            self.assertFalse(manager.auth_failed)

            # Next call is allowed to try again
            login_mock.return_value = True
            res3 = manager.ensure_logged_in()
            self.assertTrue(res3)
            self.assertEqual(login_mock.call_count, 4)

    def test_tls_verification_enabled(self):
        """Ensure session has verify=True and standard HTTPAdapter (no disabled verification)."""
        self.assertTrue(self.client.session.verify)
        adapter = self.client.session.get_adapter("https://online.yildiz.edu.tr")
        self.assertIsInstance(adapter, requests.adapters.HTTPAdapter)


if __name__ == "__main__":
    unittest.main()

