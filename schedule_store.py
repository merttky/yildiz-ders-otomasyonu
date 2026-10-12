"""
YILDIZ Ders Otomasyonu - Schedule Storage
Handles validation, sorting, atomic file writing, and locale-independent day mapping.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import SCHEDULE_FILE
from turkish_utils import turkish_casefold

logger = logging.getLogger(__name__)

# Canonical day list (0 = Monday, ..., 6 = Sunday)
DAYS_CANONICAL = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday"
]

# Turkish names mapping
DAY_TR_NAMES = {
    "Monday": "Pazartesi",
    "Tuesday": "Salı",
    "Wednesday": "Çarşamba",
    "Thursday": "Perşembe",
    "Friday": "Cuma",
    "Saturday": "Cumartesi",
    "Sunday": "Pazar"
}

# Lookup dictionary for normalizing day inputs (case-insensitive, TR and EN)
_DAY_LOOKUP: Dict[str, str] = {}
for en_day, tr_day in DAY_TR_NAMES.items():
    _DAY_LOOKUP[en_day.lower()] = en_day
    _DAY_LOOKUP[turkish_casefold(tr_day)] = en_day
    # Short aliases
    _DAY_LOOKUP[en_day[:3].lower()] = en_day
    _DAY_LOOKUP[turkish_casefold(tr_day[:3])] = en_day

TIME_REGEX = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')


def get_canonical_day(day_input: str) -> Optional[str]:
    """
    Normalizes day name (Turkish or English, any case) to canonical English name.
    Example:
        'Pazartesi' -> 'Monday'
        'monday'    -> 'Monday'
        'Salı'      -> 'Tuesday'
    """
    if not day_input or not isinstance(day_input, str):
        return None
    key = turkish_casefold(day_input.strip())
    if key in _DAY_LOOKUP:
        return _DAY_LOOKUP[key]
    key_en = day_input.strip().lower()
    return _DAY_LOOKUP.get(key_en)


def get_current_day(dt: Optional[datetime.datetime] = None) -> str:
    """
    Returns canonical day name ('Monday'..'Sunday') for given datetime or now.
    Completely locale-independent by using datetime.weekday().
    """
    target_dt = dt or datetime.datetime.now()
    return DAYS_CANONICAL[target_dt.weekday()]


def get_day_display_name(day_str: str) -> str:
    """Returns dual-language label, e.g. 'Monday (Pazartesi)'"""
    canonical = get_canonical_day(day_str) or day_str
    tr = DAY_TR_NAMES.get(canonical, "")
    return f"{canonical} ({tr})" if tr else canonical


def validate_hour(hour_str: str) -> bool:
    """Validates HH:MM format (24-hour)."""
    if not isinstance(hour_str, str):
        return False
    return bool(TIME_REGEX.match(hour_str.strip()))


def validate_lesson_entry(lesson: dict) -> Optional[dict]:
    """
    Validates and normalizes a single lesson dictionary.
    Returns cleaned dict or None if invalid.
    """
    if not isinstance(lesson, dict):
        return None

    hour = str(lesson.get("hour", "")).strip()
    if not validate_hour(hour):
        logger.warning(f"Skipping lesson with invalid hour format: {lesson}")
        return None

    desc = str(lesson.get("desc", "")).strip()
    program_no = lesson.get("program_no")
    cleaned_prog = str(program_no).strip() if program_no not in (None, "") else None

    result = {
        "hour": hour,
        "desc": desc,
    }
    if cleaned_prog:
        result["program_no"] = cleaned_prog
    return result


def sort_schedule(schedule: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    """Sorts schedule days canonically and lessons chronologically by hour."""
    sorted_schedule: Dict[str, List[dict]] = {}

    for day in DAYS_CANONICAL:
        if day in schedule and schedule[day]:
            # Sort lessons by hour string "HH:MM"
            lessons = sorted(schedule[day], key=lambda x: x.get("hour", "00:00"))
            sorted_schedule[day] = lessons

    # Retain any extra custom day keys if any exist
    for day, lessons in schedule.items():
        if day not in sorted_schedule and lessons:
            sorted_schedule[day] = sorted(lessons, key=lambda x: x.get("hour", "00:00"))

    return sorted_schedule


def load_schedule(file_path: Optional[Path] = None) -> Dict[str, List[dict]]:
    """
    Loads schedule from JSON file with corruption resilience.
    If file is corrupted, creates a backup and returns an empty schedule.
    """
    target_path = Path(file_path or SCHEDULE_FILE)
    if not target_path.exists():
        return {}

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Failed to parse schedule file {target_path}: {e}")
        # Create a backup of corrupt file to avoid data loss
        backup_path = target_path.with_suffix(
            f".corrupt.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        try:
            shutil.copy2(target_path, backup_path)
            logger.warning(f"Corrupt schedule backed up to: {backup_path}")
        except Exception as copy_err:
            logger.error(f"Failed to backup corrupt schedule: {copy_err}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error loading schedule {target_path}: {e}")
        return {}

    if not isinstance(raw_data, dict):
        logger.warning(f"Schedule root is not a dictionary: {type(raw_data)}")
        return {}

    # Validate and normalize keys and entries
    cleaned_schedule: Dict[str, List[dict]] = {}
    for day_raw, lesson_list in raw_data.items():
        canonical_day = get_canonical_day(str(day_raw))
        if not canonical_day:
            logger.warning(f"Unrecognized day in schedule: {day_raw}")
            continue

        if not isinstance(lesson_list, list):
            continue

        valid_lessons = []
        for item in lesson_list:
            validated = validate_lesson_entry(item)
            if validated:
                valid_lessons.append(validated)

        if valid_lessons:
            cleaned_schedule.setdefault(canonical_day, []).extend(valid_lessons)

    return sort_schedule(cleaned_schedule)


def save_schedule(
    schedule: Dict[str, List[dict]],
    file_path: Optional[Path] = None
) -> None:
    """
    Saves schedule atomically using a temporary file and replace.
    Ensures file is sorted and formatted.
    """
    target_path = Path(file_path or SCHEDULE_FILE)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_data = sort_schedule(schedule)

    # Write atomically via temp file in same directory
    temp_dir = target_path.parent
    temp_fd, temp_path_str = tempfile.mkstemp(
        prefix="schedule_",
        suffix=".tmp",
        dir=temp_dir
    )
    temp_path = Path(temp_path_str)

    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(sorted_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Atomic replacement
        os.replace(temp_path, target_path)

        # Set permissions (read/write for owner)
        try:
            os.chmod(target_path, 0o644)
        except OSError:
            pass

        logger.debug(f"Schedule atomically saved to: {target_path}")

    except Exception as e:
        logger.error(f"Failed to write schedule to {target_path}: {e}")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def add_lesson(
    day: str,
    hour: str,
    desc: str,
    program_no: Optional[str] = None,
    file_path: Optional[Path] = None
) -> bool:
    """
    Adds a validated lesson to the schedule and saves atomically.
    Prevents exact duplicates.
    """
    canonical_day = get_canonical_day(day)
    if not canonical_day:
        raise ValueError(f"Geçersiz gün adı: {day}")

    hour_clean = hour.strip()
    if not validate_hour(hour_clean):
        raise ValueError("Saat formatı HH:MM olmalıdır (örn: 09:30)")

    schedule = load_schedule(file_path)
    day_lessons = schedule.setdefault(canonical_day, [])

    # Check for duplicate
    for existing in day_lessons:
        if existing.get("hour") == hour_clean and existing.get("desc") == desc.strip():
            logger.info(f"Lesson already exists: {canonical_day} {hour_clean} - {desc}")
            return False

    new_lesson = {
        "hour": hour_clean,
        "desc": desc.strip(),
    }
    if program_no:
        new_lesson["program_no"] = str(program_no).strip()

    day_lessons.append(new_lesson)
    save_schedule(schedule, file_path)
    return True


def delete_lesson(
    day: str,
    index: int,
    file_path: Optional[Path] = None
) -> bool:
    """Deletes lesson at given index for day."""
    canonical_day = get_canonical_day(day)
    if not canonical_day:
        return False

    schedule = load_schedule(file_path)
    if canonical_day not in schedule:
        return False

    day_lessons = schedule[canonical_day]
    if index < 0 or index >= len(day_lessons):
        return False

    del day_lessons[index]
    if not day_lessons:
        del schedule[canonical_day]

    save_schedule(schedule, file_path)
    return True
