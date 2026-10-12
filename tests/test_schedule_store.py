import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path

from schedule_store import (
    DAYS_CANONICAL,
    add_lesson,
    delete_lesson,
    get_canonical_day,
    get_current_day,
    load_schedule,
    save_schedule,
    validate_hour,
)


class TestScheduleStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_schedule_file = Path(self.temp_dir.name) / "test_schedule.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_canonical_day_mapping(self):
        self.assertEqual(get_canonical_day("Monday"), "Monday")
        self.assertEqual(get_canonical_day("monday"), "Monday")
        self.assertEqual(get_canonical_day("Pazartesi"), "Monday")
        self.assertEqual(get_canonical_day("pazartesi"), "Monday")
        self.assertEqual(get_canonical_day("Salı"), "Tuesday")
        self.assertEqual(get_canonical_day("SALI"), "Tuesday")
        self.assertEqual(get_canonical_day("Cuma"), "Friday")
        self.assertIsNone(get_canonical_day("InvalidDay"))

    def test_locale_independent_day(self):
        # 2026-10-12 is a Monday
        mon = datetime.datetime(2026, 10, 12, 10, 0)
        self.assertEqual(get_current_day(mon), "Monday")

        # 2026-10-13 is a Tuesday
        tue = datetime.datetime(2026, 10, 13, 10, 0)
        self.assertEqual(get_current_day(tue), "Tuesday")

    def test_validate_hour(self):
        self.assertTrue(validate_hour("09:00"))
        self.assertTrue(validate_hour("23:59"))
        self.assertTrue(validate_hour("00:00"))
        self.assertFalse(validate_hour("24:00"))
        self.assertFalse(validate_hour("9:00"))
        self.assertFalse(validate_hour("09:60"))
        self.assertFalse(validate_hour("invalid"))

    def test_atomic_save_and_load(self):
        sample = {
            "Tuesday": [{"hour": "14:00", "desc": "Fizik 1"}],
            "Monday": [
                {"hour": "13:00", "desc": "Atatürk İlkeleri"},
                {"hour": "09:00", "desc": "Matematik 1", "program_no": "130766"},
            ],
        }
        save_schedule(sample, self.test_schedule_file)
        loaded = load_schedule(self.test_schedule_file)

        # Days should be sorted canonically (Monday before Tuesday)
        days = list(loaded.keys())
        self.assertEqual(days, ["Monday", "Tuesday"])

        # Lessons within Monday should be sorted chronologically by hour (09:00 before 13:00)
        mon_hours = [l["hour"] for l in loaded["Monday"]]
        self.assertEqual(mon_hours, ["09:00", "13:00"])

    def test_corrupt_file_handling(self):
        # Write corrupted JSON to file
        with open(self.test_schedule_file, "w", encoding="utf-8") as f:
            f.write("{\ninvalid json data here\n")

        # load_schedule should not crash; it should return {} and create a backup
        loaded = load_schedule(self.test_schedule_file)
        self.assertEqual(loaded, {})

        # Verify a backup was created in directory
        backup_files = list(Path(self.temp_dir.name).glob("*.corrupt.*.json"))
        self.assertTrue(len(backup_files) >= 1)

    def test_add_and_delete_lesson(self):
        # Add lesson
        res = add_lesson("Monday", "10:30", "Kimya 1", file_path=self.test_schedule_file)
        self.assertTrue(res)

        # Duplicate should return False
        dup = add_lesson("Monday", "10:30", "Kimya 1", file_path=self.test_schedule_file)
        self.assertFalse(dup)

        # Add another lesson
        add_lesson("Monday", "08:30", "Matematik", file_path=self.test_schedule_file)

        loaded = load_schedule(self.test_schedule_file)
        self.assertEqual(len(loaded["Monday"]), 2)
        # Should be sorted: 08:30 then 10:30
        self.assertEqual(loaded["Monday"][0]["hour"], "08:30")

        # Delete lesson at index 0 (08:30)
        del_res = delete_lesson("Monday", 0, file_path=self.test_schedule_file)
        self.assertTrue(del_res)

        loaded_after = load_schedule(self.test_schedule_file)
        self.assertEqual(len(loaded_after["Monday"]), 1)
        self.assertEqual(loaded_after["Monday"][0]["hour"], "10:30")


if __name__ == "__main__":
    unittest.main()
