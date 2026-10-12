"""
YILDIZ Ders Otomasyonu - Zoom Launcher
Cross-platform Zoom URL opener with direct protocol launching, host validation,
and Windows os.startfile support (bypassing cmd.exe & character splitting).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import webbrowser
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

logger = logging.getLogger(__name__)

# Allowed Zoom domains
ALLOWED_ZOOM_DOMAIN_PATTERN = re.compile(
    r'^(?:[a-zA-Z0-9-]+\.)*zoom\.us$',
    re.IGNORECASE
)


def is_valid_zoom_host(host: str) -> bool:
    """Verifies that the hostname belongs to zoom.us or a subdomain."""
    if not host:
        return False
    # Strip port if present
    clean_host = host.split(':')[0].strip().lower()
    return bool(ALLOWED_ZOOM_DOMAIN_PATTERN.match(clean_host))


def convert_to_zoom_protocol(zoom_url: str) -> Optional[str]:
    """
    Convert https://*.zoom.us URL to zoommtg:// protocol.
    Bypasses the browser's "Open Zoom?" prompt.

    Supported formats:
        https://zoom.us/j/123456789?pwd=abc
        https://yildiz-edu-tr.zoom.us/w/123456789?tk=xyz&pwd=abc
        https://sub.zoom.us/wc/123456789/join?pwd=abc

    Returns:
        zoommtg:// protocol URL or None
    """
    if not zoom_url:
        return None

    try:
        parsed = urlparse(zoom_url.strip())
        host = parsed.netloc or 'zoom.us'

        if not is_valid_zoom_host(host):
            logger.warning(f"URL is not a recognized Zoom host: {host}")
            return None

        path_parts = [p for p in parsed.path.split('/') if p]

        # Extract meeting ID from path (/j/123, /w/123, /wc/123, /join/123, etc.)
        meeting_id = None
        for i, part in enumerate(path_parts):
            if part in ['j', 'w', 'wc', 'join'] and i + 1 < len(path_parts):
                next_part = path_parts[i + 1]
                if next_part.isdigit() and len(next_part) >= 9:
                    meeting_id = next_part
                    break
            elif part.isdigit() and len(part) >= 9:
                meeting_id = part
                break

        if not meeting_id:
            logger.warning("Could not extract meeting ID from Zoom URL")
            return None

        # Extract query parameters
        params = parse_qs(parsed.query)
        password = params.get('pwd', [''])[0]
        token = params.get('tk', [''])[0]

        # Build Zoom protocol URL with URL-encoded parameters
        protocol_url = f"zoommtg://{host}/join?confno={meeting_id}"

        if password:
            protocol_url += f"&pwd={quote(password)}"

        if token:
            protocol_url += f"&tk={quote(token)}"

        return protocol_url

    except Exception as e:
        logger.warning(f"Failed to convert to Zoom protocol: {e}")
        return None


def open_with_protocol(zoom_protocol_url: str) -> bool:
    """
    Open Zoom using protocol handler (zoommtg://).
    On Windows: uses os.startfile directly, eliminating cmd.exe '&' separator bugs.
    On macOS: uses 'open' command.
    On Linux: uses 'xdg-open' command.
    """
    try:
        if sys.platform == "win32":
            # On Windows, os.startfile calls ShellExecuteW directly without cmd.exe
            if hasattr(os, "startfile"):
                os.startfile(zoom_protocol_url)
                logger.info("✓ Zoom opened via os.startfile (Windows)")
                return True
            else:
                # Fallback if startfile is not available
                result = subprocess.run(
                    ["explorer.exe", zoom_protocol_url],
                    capture_output=True,
                    timeout=5
                )
                return result.returncode == 0

        elif sys.platform == "darwin":  # macOS
            result = subprocess.run(
                ["open", zoom_protocol_url],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info("✓ Zoom opened (macOS)")
                return True
            else:
                logger.warning(f"open command returned {result.returncode}: {result.stderr}")
                return False

        elif sys.platform.startswith("linux"):  # Linux
            result = subprocess.run(
                ["xdg-open", zoom_protocol_url],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info("✓ Zoom opened (Linux)")
                return True
            else:
                logger.warning(f"xdg-open returned {result.returncode}: {result.stderr}")
                return False

        else:
            logger.warning(f"Unsupported platform for direct protocol: {sys.platform}")
            return False

    except subprocess.TimeoutExpired:
        logger.warning("Zoom open command timed out (likely launched successfully)")
        return True
    except Exception as e:
        logger.error(f"Failed to open with protocol handler: {e}")
        return False


def open_with_browser(zoom_url: str) -> bool:
    """
    Fallback: Open Zoom URL in default browser.
    """
    try:
        webbrowser.open(zoom_url)
        logger.info("✓ Zoom link opened in browser (fallback)")
        return True
    except Exception as e:
        logger.error(f"Failed to open in browser: {e}")
        return False


def open_zoom_link(zoom_url: str) -> bool:
    """
    Opens Zoom meeting link using protocol handler first, with fallback to browser.
    """
    if not zoom_url:
        logger.error("Zoom URL is empty")
        return False

    logger.info(f"Opening Zoom link: {zoom_url[:70]}...")

    # Attempt protocol launch
    zoom_protocol_url = convert_to_zoom_protocol(zoom_url)
    if zoom_protocol_url:
        logger.debug(f"Attempting protocol launch: {zoom_protocol_url[:70]}...")
        if open_with_protocol(zoom_protocol_url):
            return True
        logger.info("Protocol launch did not succeed; falling back to default browser")

    # Fallback to browser
    return open_with_browser(zoom_url)
