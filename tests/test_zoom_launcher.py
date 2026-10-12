import unittest
from zoom_launcher import convert_to_zoom_protocol, is_valid_zoom_host


class TestZoomLauncher(unittest.TestCase):
    def test_valid_zoom_host(self):
        self.assertTrue(is_valid_zoom_host("zoom.us"))
        self.assertTrue(is_valid_zoom_host("yildiz-edu-tr.zoom.us"))
        self.assertTrue(is_valid_zoom_host("us02web.zoom.us"))
        self.assertTrue(is_valid_zoom_host("ZOOM.US"))
        self.assertTrue(is_valid_zoom_host("zoom.us:443"))

        self.assertFalse(is_valid_zoom_host("evil-zoom.us.attacker.com"))
        self.assertFalse(is_valid_zoom_host("notzoom.org"))
        self.assertFalse(is_valid_zoom_host(""))

    def test_convert_to_zoom_protocol(self):
        # Standard meeting url
        url1 = "https://zoom.us/j/12345678901?pwd=mypassword123"
        proto1 = convert_to_zoom_protocol(url1)
        self.assertIsNotNone(proto1)
        self.assertTrue(proto1.startswith("zoommtg://zoom.us/join?confno=12345678901"))
        self.assertIn("pwd=mypassword123", proto1)

        # Subdomain with token and special characters in password
        url2 = "https://yildiz-edu-tr.zoom.us/w/98765432100?tk=token_abc&pwd=pass%20word&"
        proto2 = convert_to_zoom_protocol(url2)
        self.assertIsNotNone(proto2)
        self.assertTrue(proto2.startswith("zoommtg://yildiz-edu-tr.zoom.us/join?confno=98765432100"))
        self.assertIn("tk=token_abc", proto2)

        # Malicious non-zoom url
        bad_url = "https://malicious.com/j/12345678901?pwd=abc"
        self.assertIsNone(convert_to_zoom_protocol(bad_url))

        # URL without meeting ID
        no_id = "https://zoom.us/about"
        self.assertIsNone(convert_to_zoom_protocol(no_id))


if __name__ == "__main__":
    unittest.main()
