import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import credentials


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_creds_file = Path(self.temp_dir.name) / "credentials.json"
        self.orig_creds_file = credentials.CREDENTIALS_FILE
        credentials.CREDENTIALS_FILE = self.test_creds_file

    def tearDown(self):
        credentials.CREDENTIALS_FILE = self.orig_creds_file
        self.temp_dir.cleanup()

    def test_save_and_load_fallback(self):
        # Test saving and loading without keyring (pure fallback mode)
        with patch.object(credentials, "KEYRING_AVAILABLE", False):
            # Notice intentional whitespace in password to verify it is NOT stripped
            credentials.save_credentials(
                username="  user@yildiz.edu.tr  ",
                password=" secret password with spaces ",
                webhook_url="https://discord.com/webhook/test"
            )

            u, p, w = credentials.load_credentials()
            # Username is stripped
            self.assertEqual(u, "user@yildiz.edu.tr")
            # Password preserves whitespace!
            self.assertEqual(p, " secret password with spaces ")
            self.assertEqual(w, "https://discord.com/webhook/test")

            # Check file permissions (0600 on POSIX)
            if os.name != 'nt':
                mode = oct(self.test_creds_file.stat().st_mode & 0o777)
                self.assertEqual(mode, '0o600')

    def test_save_webhook_url(self):
        with patch.object(credentials, "KEYRING_AVAILABLE", False):
            credentials.save_webhook_url("https://discord.com/api/test")
            self.assertEqual(credentials.load_webhook_url(), "https://discord.com/api/test")


if __name__ == "__main__":
    unittest.main()
