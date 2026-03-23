"""
YILDIZ Ders Otomasyonu - Configuration
"""
import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.absolute()
SCHEDULE_FILE = BASE_DIR / "schedule.json"
USER_FILE = BASE_DIR / "user_info"
HISTORY_FILE = BASE_DIR / "join_history.json"
SESSION_FILE = BASE_DIR / ".ytu_session"
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

# ── Scheduler Settings ───────────────────────────────────────────────────────
CHECK_INTERVAL = 20  # Check every 20 seconds
JOIN_TOLERANCE = 30   # Join if within ±30 seconds of class time

# ── Retry Settings ───────────────────────────────────────────────────────────
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY = 2  # seconds between retries

# ── Logging Settings ─────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── Color Codes (for terminal output) ───────────────────────────────────────
class Colors:
    """ANSI color codes for pretty terminal output"""
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

# ── HTML Selectors (from OCAv2.6 analysis) ───────────────────────────────────
SELECTORS = {
    "activity_tab": "ETKİNLİK AKIŞI",  # Tab text to find
    "active_class_color": "rgba(0, 81, 146, 1)",  # Blue timeline color
    "join_button_text": "Derse Katıl",  # Join button text
}
