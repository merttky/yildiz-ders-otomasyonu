"""
YILDIZ Ders Otomasyonu - Configuration
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# ── App Info ─────────────────────────────────────────────────────────────────
APP_NAME = "YILDIZ Ders Otomasyonu"
APP_VERSION = "v2.0.0"

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.absolute()
SCHEDULE_FILE = BASE_DIR / "schedule.json"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
LEGACY_USER_FILE = BASE_DIR / "user_info"
HISTORY_FILE = BASE_DIR / "join_history.json"
SESSION_FILE = BASE_DIR / ".ytu_session.json"
LEGACY_SESSION_FILE = BASE_DIR / ".ytu_session"
LOG_FILE = BASE_DIR / "automation.log"

# ── YTU Online Settings ──────────────────────────────────────────────────────
YTU_BASE_URL = "https://online.yildiz.edu.tr"
YTU_LOGIN_URL = f"{YTU_BASE_URL}/Account/Login"
YTU_COCKPIT_URL = f"{YTU_BASE_URL}/?transaction=LMS.CORE.Cockpit.ViewCockpit/0"

# ── HTTP Request Settings ────────────────────────────────────────────────────
REQUEST_TIMEOUT = 15  # seconds
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
SESSION_LIFETIME = 7200  # 2 hours (7200 seconds)
SESSION_CACHE_TTL = 60   # Seconds to trust in-memory session without re-validating

# ── Scheduler Window Settings ────────────────────────────────────────────────
CHECK_INTERVAL = 20            # Check loop interval in seconds
SCHEDULER_JOIN_BEFORE = 5 * 60  # Start looking 5 minutes before class
SCHEDULER_JOIN_AFTER = 45 * 60  # Catch-up / retry window up to 45 mins after start
SCHEDULER_RETRY_INTERVAL = 25  # Seconds between retries while link is not ready
JOIN_TOLERANCE = 30            # Deprecated alias kept for backwards compatibility

# ── Manual Join ("Şimdi Derse Gir") Settings ────────────────────────────────
MANUAL_JOIN_BEFORE = 15 * 60  # 15 minutes before class
MANUAL_JOIN_AFTER = 60 * 60   # 60 minutes after class starts

# ── Retry Settings ───────────────────────────────────────────────────────────
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY = 2  # seconds between retries

# ── Logging Settings ─────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT = 3

# ── HTML Selectors ───────────────────────────────────────────────────────────
SELECTORS = {
    "activity_tab": "ETKİNLİK AKIŞI",
    "active_class_color": "rgba(0, 81, 146, 1)",
    "join_button_text": "Derse Katıl",
}


def setup_logging(
    log_file: Optional[Path] = None,
    log_level: str = "INFO"
) -> logging.Logger:
    """
    Centralized logging initialization.
    Configures RotatingFileHandler with UTF-8 and console StreamHandler.
    Safe to call multiple times (idempotent).
    """
    target_log_file = log_file or LOG_FILE
    target_log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # Rotating file handler (UTF-8, 5MB limit, 3 backups)
    file_handler = RotatingFileHandler(
        target_log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(numeric_level)
    root_logger.addHandler(file_handler)

    # Console stream handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)
    root_logger.addHandler(console_handler)

    return logging.getLogger("ytu_automation")
