"""
Discord Webhook Notifier for YILDIZ Ders Otomasyonu
Provides non-blocking (asynchronous) webhook notifications and uses timezone-aware timestamps.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Color constants for Discord embeds
COLOR_SUCCESS = 0x2ECC71  # Green
COLOR_WARNING = 0xF1C40F  # Yellow
COLOR_ERROR = 0xE74C3C    # Red
COLOR_INFO = 0x3498DB     # Blue

# Background thread pool executor for non-blocking notifications
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="discord_notify")


def _send_notification_sync(
    webhook_url: str,
    title: str,
    message: str,
    color: int = COLOR_INFO,
    course_name: Optional[str] = None,
    hour: Optional[str] = None,
    timeout: int = 5
) -> bool:
    """Synchronous implementation of webhook dispatch."""
    if not webhook_url:
        return False

    try:
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

        embed = {
            "title": title,
            "description": message,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {
                "text": "YILDIZ Ders Otomasyonu"
            }
        }

        if fields:
            embed["fields"] = fields

        payload = {"embeds": [embed]}

        response = requests.post(
            webhook_url,
            json=payload,
            timeout=timeout
        )

        if response.status_code in (200, 204):
            logger.debug(f"Discord notification sent: {title}")
            return True
        else:
            logger.warning(f"Discord notification returned status: {response.status_code}")
            return False

    except requests.exceptions.RequestException as e:
        logger.warning(f"Discord notification request error: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected Discord notification error: {e}")
        return False


def send_notification(
    webhook_url: str,
    title: str,
    message: str,
    color: int = COLOR_INFO,
    course_name: Optional[str] = None,
    hour: Optional[str] = None,
    async_send: bool = True
) -> bool:
    """
    Send a notification to Discord via webhook.
    By default (async_send=True), executes on background thread pool to avoid blocking.
    """
    if not webhook_url:
        return False

    if async_send:
        _executor.submit(
            _send_notification_sync,
            webhook_url,
            title,
            message,
            color,
            course_name,
            hour,
            8
        )
        return True
    else:
        return _send_notification_sync(
            webhook_url,
            title,
            message,
            color,
            course_name,
            hour,
            8
        )


def notify_lesson_joined(webhook_url: str, course_name: str, hour: Optional[str] = None) -> bool:
    """Notify when successfully joined a lesson."""
    return send_notification(
        webhook_url=webhook_url,
        title="Derse Katılım Başarılı",
        message="Zoom uygulaması açıldı!",
        color=COLOR_SUCCESS,
        course_name=course_name,
        hour=hour
    )


def notify_lesson_failed(webhook_url: str, course_name: str, error: str, hour: Optional[str] = None) -> bool:
    """Notify when failed to join a lesson."""
    return send_notification(
        webhook_url=webhook_url,
        title="Derse Katılım Başarısız",
        message=f"Hata: {error}",
        color=COLOR_ERROR,
        course_name=course_name,
        hour=hour
    )


def notify_waiting_for_link(webhook_url: str, course_name: str, hour: str) -> bool:
    """Notify when lesson window starts and waiting for teacher to start meeting."""
    return send_notification(
        webhook_url=webhook_url,
        title="Ders Saati Geldi",
        message="Ders takip penceresi başladı, Zoom linki bekleniyor...",
        color=COLOR_INFO,
        course_name=course_name,
        hour=hour
    )


def notify_still_waiting_for_link(
    webhook_url: str,
    course_name: str,
    hour: str,
    minutes_elapsed: int = 15
) -> bool:
    """Notify when lesson started 15+ minutes ago and Zoom meeting is still not opened."""
    return send_notification(
        webhook_url=webhook_url,
        title="Ders Linki Hâlâ Bekleniyor",
        message=f"Ders başlangıcından {minutes_elapsed} dakika geçti, toplantı henüz açılmadı. Kontrol edilmeye devam ediliyor...",
        color=COLOR_WARNING,
        course_name=course_name,
        hour=hour
    )


def notify_lesson_missed(
    webhook_url: str,
    course_name: str,
    hour: str,
    reason: str = "Ders süresi aşıldı, aktif Zoom linki açılmadı"
) -> bool:
    """Notify when catch-up window closes without finding an active meeting."""
    return send_notification(
        webhook_url=webhook_url,
        title="Ders Kaçırıldı / Açılmadı",
        message=f"{reason}. Takip penceresi sona erdi.",
        color=COLOR_ERROR,
        course_name=course_name,
        hour=hour
    )


def notify_auth_failed(
    webhook_url: str,
    course_name: Optional[str] = None,
    hour: Optional[str] = None
) -> bool:
    """Notify once when authentication fails, informing that auto-retry is halted to prevent account lockout."""
    return send_notification(
        webhook_url=webhook_url,
        title="Giriş Başarısız (Hesap Koruması Devrede)",
        message="Kullanıcı adı veya şifre doğrulanamadı. Üniversite hesabınızın kilitlenmesini önlemek için otomatik giriş denemeleri durduruldu!",
        color=COLOR_ERROR,
        course_name=course_name,
        hour=hour
    )


def notify_scheduler_triggered(webhook_url: str, course_name: str, hour: str) -> bool:
    """Notify when scheduler triggers a lesson join (alias for notify_waiting_for_link)."""
    return notify_waiting_for_link(webhook_url, course_name, hour)


def notify_no_link_found(webhook_url: str, course_name: Optional[str] = None) -> bool:
    """Notify when no Zoom link was found (for manual join button)."""
    return send_notification(
        webhook_url=webhook_url,
        title="Zoom Linki Bulunamadı",
        message="Aktif ders bulunamadı veya link henüz açılmadı.",
        color=COLOR_WARNING,
        course_name=course_name
    )


def test_webhook(webhook_url: str) -> bool:
    """Send a test notification synchronously to verify webhook works."""
    return _send_notification_sync(
        webhook_url=webhook_url,
        title="Test Bildirimi",
        message="Discord webhook bağlantısı başarılı!",
        color=COLOR_SUCCESS,
        timeout=6
    )
