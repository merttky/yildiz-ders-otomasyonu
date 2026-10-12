"""
YILDIZ Ders Otomasyonu - Credentials Management
Uses system keyring (macOS Keychain, Windows Credential Manager, Linux SecretService)
with automatic migration from legacy shelve storage and safe fallback.
"""
from __future__ import annotations

import json
import logging
import os
import shelve
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from config import CREDENTIALS_FILE, LEGACY_USER_FILE

logger = logging.getLogger(__name__)

SERVICE_NAME = "YILDIZ_Ders_Otomasyonu"

# Try importing keyring; if missing, fallback is handled gracefully
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    KEYRING_AVAILABLE = False


def _set_file_permissions(path: Path, mode: int = 0o600) -> None:
    """Set restrictive file permissions (owner only read/write)."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _save_json_atomic(data: dict, file_path: Path) -> None:
    """Saves dictionary to JSON atomically with 0600 permissions."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = file_path.parent
    temp_fd, temp_path_str = tempfile.mkstemp(
        prefix="cred_",
        suffix=".tmp",
        dir=temp_dir
    )
    temp_path = Path(temp_path_str)

    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        _set_file_permissions(temp_path, 0o600)
        os.replace(temp_path, file_path)
        _set_file_permissions(file_path, 0o600)
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise e


def _load_json_settings() -> dict:
    """Loads non-secret settings from credentials.json."""
    if not CREDENTIALS_FILE.exists():
        return {}
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to read credentials.json: {e}")
        return {}


def migrate_legacy_shelve_if_needed() -> None:
    """
    Checks for legacy shelve database 'user_info' and migrates
    username, password, and discord_webhook_url to secure storage.
    """
    # Shelve might exist as user_info, user_info.db, or user_info.dat
    candidates = [
        LEGACY_USER_FILE,
        LEGACY_USER_FILE.with_suffix(".db"),
        LEGACY_USER_FILE.with_suffix(".dat"),
    ]
    legacy_path = None
    for c in candidates:
        if c.exists():
            legacy_path = c
            break

    if not legacy_path:
        return

    logger.info(f"Detected legacy shelve credentials: {legacy_path}. Starting migration...")
    username = ""
    password = ""
    webhook_url = ""

    try:
        with shelve.open(str(LEGACY_USER_FILE)) as db:
            username = str(db.get("username", "") or "")
            password = str(db.get("password", "") or "")
            webhook_url = str(db.get("discord_webhook_url", "") or "")
    except Exception as e:
        logger.warning(f"Could not read legacy shelve database: {e}")
        return

    if username or webhook_url:
        save_credentials(username=username, password=password, webhook_url=webhook_url)
        logger.info("✓ Credentials migrated to new storage format")

        # Safely archive legacy files so plain-text password is not left lying around
        for c in candidates:
            if c.exists():
                try:
                    migrated_name = c.with_name(f"{c.name}.migrated")
                    os.replace(c, migrated_name)
                    _set_file_permissions(migrated_name, 0o600)
                    logger.info(f"Archived {c.name} to {migrated_name.name}")
                except Exception as e:
                    logger.warning(f"Could not archive legacy file {c}: {e}")


def save_credentials(
    username: str,
    password: str,
    webhook_url: Optional[str] = None
) -> None:
    """
    Saves username and discord webhook url to credentials.json (0600).
    Saves password to system keyring (or fallback inside credentials.json if keyring unavailable).
    Note: Password is NOT stripped to preserve intentional whitespace.
    """
    username_clean = username.strip() if username else ""
    settings = _load_json_settings()

    settings["username"] = username_clean
    if webhook_url is not None:
        settings["discord_webhook_url"] = webhook_url.strip()

    password_saved_in_keyring = False

    if KEYRING_AVAILABLE and username_clean:
        try:
            keyring.set_password(SERVICE_NAME, username_clean, password)
            password_saved_in_keyring = True
            logger.debug(f"Password stored in system keyring for: {username_clean}")
            # If previously stored in json fallback, remove it
            if "fallback_password" in settings:
                del settings["fallback_password"]
        except Exception as e:
            logger.warning(f"Keyring failed to save password ({e}); using safe fallback file (0600)")

    if not password_saved_in_keyring:
        # Fallback to local 0600 permissions JSON
        settings["fallback_password"] = password

    _save_json_atomic(settings, CREDENTIALS_FILE)
    logger.info(f"Credentials saved for user: {username_clean}")


def load_credentials() -> Tuple[str, str, str]:
    """
    Returns (username, password, discord_webhook_url).
    Checks legacy migration first if needed.
    """
    # Check if migration is needed
    if not CREDENTIALS_FILE.exists() and LEGACY_USER_FILE.exists():
        migrate_legacy_shelve_if_needed()

    settings = _load_json_settings()
    username = settings.get("username", "")
    webhook_url = settings.get("discord_webhook_url", "")
    password = ""

    if KEYRING_AVAILABLE and username:
        try:
            stored_pwd = keyring.get_password(SERVICE_NAME, username)
            if stored_pwd is not None:
                password = stored_pwd
        except Exception as e:
            logger.warning(f"Keyring failed to load password: {e}")

    # Check fallback password if keyring didn't return one
    if not password and "fallback_password" in settings:
        password = settings.get("fallback_password", "")

    return username, password, webhook_url


def save_webhook_url(url: str) -> None:
    """Saves or updates only the Discord webhook URL."""
    settings = _load_json_settings()
    settings["discord_webhook_url"] = url.strip()
    _save_json_atomic(settings, CREDENTIALS_FILE)


def load_webhook_url() -> str:
    """Returns the saved Discord webhook URL."""
    settings = _load_json_settings()
    return settings.get("discord_webhook_url", "")


def clear_credentials() -> None:
    """Removes stored credentials."""
    username, _, _ = load_credentials()
    if KEYRING_AVAILABLE and username:
        try:
            keyring.delete_password(SERVICE_NAME, username)
        except Exception:
            pass

    if CREDENTIALS_FILE.exists():
        try:
            CREDENTIALS_FILE.unlink()
        except OSError:
            pass
    logger.info("Credentials cleared")
