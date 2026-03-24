"""
Discord Webhook Notifier for YILDIZ Ders Otomasyonu
"""

import requests
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Color constants for Discord embeds
COLOR_SUCCESS = 0x2ECC71  # Green
COLOR_WARNING = 0xF1C40F  # Yellow
COLOR_ERROR = 0xE74C3C    # Red
COLOR_INFO = 0x3498DB     # Blue


def send_notification(
    webhook_url: str,
    title: str,
    message: str,
    color: int = COLOR_INFO,
    course_name: Optional[str] = None,
    hour: Optional[str] = None
) -> bool:
    """
    Send a notification to Discord via webhook.

    Args:
        webhook_url: Discord webhook URL
        title: Notification title
        message: Main message content
        color: Embed color (hex integer)
        course_name: Optional course name to display
        hour: Optional time to display

    Returns:
        True if successful, False otherwise
    """
    if not webhook_url:
        return False

    try:
        # Build embed fields
        fields = []

        if course_name:
            fields.append({
                "name": "Ders",
                "value": course_name,
                "inline": True
            })

        if hour:
            fields.append({
                "name": "Saat",
                "value": hour,
                "inline": True
            })

        # Build embed payload
        embed = {
            "title": title,
            "description": message,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {
                "text": "YILDIZ Ders Otomasyonu"
            }
        }

        if fields:
            embed["fields"] = fields

        payload = {
            "embeds": [embed]
        }

        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10
        )

        if response.status_code in (200, 204):
            logger.debug(f"Discord notification sent: {title}")
            return True
        else:
            logger.warning(f"Discord notification failed: {response.status_code}")
            return False

    except requests.exceptions.RequestException as e:
        logger.warning(f"Discord notification error: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected Discord error: {e}")
        return False


def notify_lesson_joined(webhook_url: str, course_name: str, hour: str = None) -> bool:
    """Notify when successfully joined a lesson."""
    return send_notification(
        webhook_url=webhook_url,
        title="Derse Katilim Basarili",
        message="Zoom uygulamasi acildi!",
        color=COLOR_SUCCESS,
        course_name=course_name,
        hour=hour
    )


def notify_lesson_failed(webhook_url: str, course_name: str, error: str, hour: str = None) -> bool:
    """Notify when failed to join a lesson."""
    return send_notification(
        webhook_url=webhook_url,
        title="Derse Katilim Basarisiz",
        message=f"Hata: {error}",
        color=COLOR_ERROR,
        course_name=course_name,
        hour=hour
    )


def notify_scheduler_triggered(webhook_url: str, course_name: str, hour: str) -> bool:
    """Notify when scheduler triggers a lesson join."""
    return send_notification(
        webhook_url=webhook_url,
        title="Ders Saati Geldi",
        message="Otomatik katilim baslatiliyor...",
        color=COLOR_INFO,
        course_name=course_name,
        hour=hour
    )


def notify_no_link_found(webhook_url: str, course_name: str = None) -> bool:
    """Notify when no Zoom link was found."""
    return send_notification(
        webhook_url=webhook_url,
        title="Zoom Linki Bulunamadi",
        message="Aktif ders bulunamadi veya link cikarilmadi.",
        color=COLOR_WARNING,
        course_name=course_name
    )


def test_webhook(webhook_url: str) -> bool:
    """Send a test notification to verify webhook works."""
    return send_notification(
        webhook_url=webhook_url,
        title="Test Bildirimi",
        message="Discord webhook baglantisi basarili!",
        color=COLOR_SUCCESS
    )
