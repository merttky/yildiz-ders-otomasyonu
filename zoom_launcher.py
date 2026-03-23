"""
YILDIZ Ders Otomasyonu - Zoom Launcher
Platform-agnostic Zoom URL opener
"""
import subprocess
import webbrowser
import logging
import sys
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


def open_zoom_link(zoom_url: str) -> bool:
    """
    Open Zoom meeting link using platform-specific method

    Approaches:
    1. Direct Zoom protocol (zoommtg://) - Bypasses "Open zoom.us?" dialog!
    2. System default browser (fallback)

    Args:
        zoom_url: Zoom meeting URL (https://zoom.us/j/123456789?pwd=abc)

    Returns:
        True if successfully opened

    Note:
        Zoom desktop app must be installed for protocol handler to work
    """
    if not zoom_url:
        logger.error("Zoom URL is empty")
        return False

    logger.info(f"Opening Zoom link: {zoom_url[:60]}...")

    try:
        # Convert to Zoom protocol URL for direct app launch
        zoom_protocol_url = convert_to_zoom_protocol(zoom_url)

        if zoom_protocol_url:
            logger.debug(f"Using Zoom protocol: {zoom_protocol_url[:60]}...")
            return open_with_protocol(zoom_protocol_url)
        else:
            # Fallback to browser
            logger.debug("Using browser fallback")
            return open_with_browser(zoom_url)

    except Exception as e:
        logger.error(f"Failed to open Zoom link: {e}")
        return False


def convert_to_zoom_protocol(zoom_url: str) -> Optional[str]:
    """
    Convert https://zoom.us URL to zoommtg:// protocol

    Supported URL formats:
        https://zoom.us/j/123456789?pwd=abc123
        https://subdomain.zoom.us/w/123456789?tk=...&pwd=abc123
        →
        zoommtg://zoom.us/join?confno=123456789&pwd=abc123

    This bypasses the "Open zoom.us?" browser dialog!
    """
    if 'zoom.us' not in zoom_url.lower():
        logger.debug("Not a zoom.us URL, cannot convert to protocol")
        return None

    try:
        parsed = urlparse(zoom_url)
        path_parts = parsed.path.split('/')

        # Extract meeting ID from path (/j/123, /w/123, /join/123, /wc/123)
        meeting_id = None
        for i, part in enumerate(path_parts):
            # Check if this is a known path segment followed by meeting ID
            if part in ['j', 'w', 'wc', 'join'] and i + 1 < len(path_parts):
                next_part = path_parts[i + 1]
                if next_part.isdigit() and len(next_part) >= 9:
                    meeting_id = next_part
                    break
            # Also check for standalone digits
            elif part.isdigit() and len(part) >= 9:
                meeting_id = part
                break

        if not meeting_id:
            logger.warning("Could not extract meeting ID from Zoom URL")
            return None

        # Extract password from query params
        params = parse_qs(parsed.query)
        password = params.get('pwd', [''])[0]

        # Also extract token if present (for some Zoom links)
        token = params.get('tk', [''])[0]

        # Use the subdomain from original URL or default to zoom.us
        host = parsed.netloc or 'zoom.us'

        # Build Zoom protocol URL
        protocol_url = f"zoommtg://{host}/join?confno={meeting_id}"

        if password:
            protocol_url += f"&pwd={password}"

        if token:
            protocol_url += f"&tk={token}"

        logger.debug(f"Converted to protocol URL: {protocol_url[:80]}...")
        return protocol_url

    except Exception as e:
        logger.warning(f"Failed to convert to Zoom protocol: {e}")
        return None


def open_with_protocol(zoom_protocol_url: str) -> bool:
    """
    Open Zoom using protocol handler (zoommtg://)

    Platform-specific commands:
    - macOS: open zoommtg://...
    - Windows: start zoommtg://...
    - Linux: xdg-open zoommtg://...
    """
    try:
        if sys.platform == "darwin":  # macOS
            result = subprocess.run(
                ['open', zoom_protocol_url],
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.info("✓ Zoom opened (macOS)")
            return result.returncode == 0

        elif sys.platform == "win32":  # Windows
            result = subprocess.run(
                ['start', '', zoom_protocol_url],
                shell=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.info("✓ Zoom opened (Windows)")
            return result.returncode == 0

        elif sys.platform.startswith("linux"):  # Linux
            result = subprocess.run(
                ['xdg-open', zoom_protocol_url],
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.info("✓ Zoom opened (Linux)")
            return result.returncode == 0

        else:
            logger.warning(f"Unsupported platform: {sys.platform}")
            return False

    except subprocess.TimeoutExpired:
        logger.warning("Zoom open command timed out (might still have worked)")
        return True  # Assume success - Zoom was likely launched

    except Exception as e:
        logger.error(f"Failed to open with protocol handler: {e}")
        return False


def open_with_browser(zoom_url: str) -> bool:
    """
    Fallback: Open Zoom URL in default browser

    Note: This will show the "Open zoom.us?" dialog in most browsers
    """
    try:
        webbrowser.open(zoom_url)
        logger.info("✓ Zoom link opened in browser (fallback)")
        return True
    except Exception as e:
        logger.error(f"Failed to open in browser: {e}")
        return False


# ── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test with a sample Zoom URL
    test_url = "https://zoom.us/j/123456789?pwd=testpassword"

    print(f"Testing Zoom launcher with: {test_url}")
    print(f"Platform: {sys.platform}")

    # Test conversion
    protocol_url = convert_to_zoom_protocol(test_url)
    print(f"Protocol URL: {protocol_url}")

    # Test opening (uncomment to actually open Zoom)
    # success = open_zoom_link(test_url)
    # print(f"Result: {'✓ Success' if success else '✗ Failed'}")
