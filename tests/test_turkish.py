import unittest
from turkish_utils import turkish_casefold, turkish_strip_accents, turkish_contains


class TestTurkishUtils(unittest.TestCase):
    def test_turkish_casefold(self):
        self.assertEqual(turkish_casefold("İSTANBUL"), "istanbul")
        self.assertEqual(turkish_casefold("IŞIK"), "ışık")
        self.assertEqual(turkish_casefold("İş Sağlığı"), "iş sağlığı")
        self.assertEqual(turkish_casefold("TÜRKÇE"), "türkçe")
        self.assertEqual(turkish_casefold(""), "")

    def test_turkish_strip_accents(self):
        self.assertEqual(turkish_strip_accents("İş Sağlığı"), "is sagligi")
        self.assertEqual(turkish_strip_accents("ÇÖĞÜŞI"), "cogusi")
        self.assertEqual(turkish_strip_accents("TÜRKÇE 1"), "turkce 1")

    def test_turkish_contains(self):
        # Exact Turkish match
        self.assertTrue(turkish_contains("İş Sağlığı", "İş Sağlığı ve Güvenliği 1"))
        self.assertTrue(turkish_contains("iş sağlığı", "İş Sağlığı ve Güvenliği 1"))
        self.assertTrue(turkish_contains("İŞ SAĞLIĞI", "İş Sağlığı ve Güvenliği 1"))

        # ASCII fuzzy match
        self.assertTrue(turkish_contains("is sagligi", "İş Sağlığı ve Güvenliği 1"))
        self.assertTrue(turkish_contains("turkce", "TÜRKÇE 1"))
        self.assertTrue(turkish_contains("TÜRKÇE", "Turkce 1"))

        # Non-match
        self.assertFalse(turkish_contains("Fizik", "Matematik 1"))
        self.assertFalse(turkish_contains("", "Matematik 1"))
        self.assertFalse(turkish_contains("Kimya", ""))


if __name__ == "__main__":
    unittest.main()
