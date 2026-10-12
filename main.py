"""
YILDIZ Ders Otomasyonu - Main GUI & Application
v2.0 - Selenium-free, Lightweight & Thread-Safe
"""
from __future__ import annotations

import datetime
import logging
import os
import sys
import threading
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

from config import (
    APP_NAME,
    APP_VERSION,
    MANUAL_JOIN_AFTER,
    MANUAL_JOIN_BEFORE,
    setup_logging,
)
from credentials import (
    clear_credentials,
    load_credentials,
    load_webhook_url,
    migrate_legacy_shelve_if_needed,
    save_credentials,
    save_webhook_url,
)
from discord_notifier import (
    notify_auth_failed,
    notify_lesson_failed,
    notify_lesson_joined,
    notify_lesson_missed,
    notify_no_link_found,
    notify_still_waiting_for_link,
    notify_waiting_for_link,
    test_webhook,
)
from schedule_store import (
    DAYS_CANONICAL,
    delete_lesson as store_delete_lesson,
    get_current_day,
    get_day_display_name,
    load_schedule,
    save_schedule,
)
from scheduler import JoinResult, LessonScheduler
from ytu_client import YTUSessionManager
from zoom_launcher import open_zoom_link

logger = logging.getLogger(__name__)


def format_delta_time(delta_sec: int) -> str:
    """Formats delta seconds into human readable Turkish time string (e.g. '12 dk', '1 sa 05 dk', '45 sn')."""
    if delta_sec < 60:
        return f"{max(0, delta_sec)} sn"
    total_minutes = delta_sec // 60
    if total_minutes < 60:
        return f"{total_minutes} dk"
    hours = total_minutes // 60
    mins = total_minutes % 60
    return f"{hours} sa {mins:02d} dk"


class AutomationApp:
    """
    Main application controller and Tkinter interface.
    All background tasks dispatch UI updates through root.after.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.resizable(False, False)

        # Thread-safe session manager
        self.session_manager: Optional[YTUSessionManager] = None
        self._automation_lock = threading.Lock()
        self._status_after_id: Optional[str] = None

        # Build UI layout
        self._setup_styles()
        self._build_ui()

        # Initialize credentials and data
        migrate_legacy_shelve_if_needed()
        self._load_saved_credentials()
        self.refresh_lesson_list()

        # Initialize background scheduler with anti-spam and circuit breaker callbacks
        self.scheduler = LessonScheduler(
            join_callback=self._scheduler_join_callback,
            notify_waiting_callback=self._on_notify_waiting,
            notify_still_waiting_callback=self._on_notify_still_waiting,
            notify_missed_callback=self._on_notify_missed,
            notify_auth_failed_callback=self._on_notify_auth_failed,
            on_auth_failed_alert=self._on_auth_failed_alert,
        )
        self.scheduler.start()
        self._update_status()

        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ── Thread-Safe UI Helpers ───────────────────────────────────────────────

    def ui_call(self, func, *args, **kwargs) -> None:
        """Schedules a function to run on the Tkinter main thread."""
        self.root.after(0, lambda: func(*args, **kwargs))

    def show_info(self, title: str, message: str) -> None:
        self.ui_call(messagebox.showinfo, title, message)

    def show_warning(self, title: str, message: str) -> None:
        self.ui_call(messagebox.showwarning, title, message)

    def show_error(self, title: str, message: str) -> None:
        self.ui_call(messagebox.showerror, title, message)

    # ── UI Construction ──────────────────────────────────────────────────────

    def _setup_styles(self) -> None:
        self.style = ttk.Style()
        self.style.configure("Green.TButton", font=("Arial", 10))
        self.style.configure("Blue.TButton", font=("Arial", 10))

    def _build_ui(self) -> None:
        # 1. Credentials section
        tk.Label(self.root, text="Okul Maili:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.username_entry = tk.Entry(self.root, width=25)
        self.username_entry.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(self.root, text="Şifre:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.password_entry = tk.Entry(self.root, show="*", width=25)
        self.password_entry.grid(row=1, column=1, padx=5, pady=5)

        tk.Button(
            self.root,
            text="Bilgileri Kaydet",
            command=self.save_user_credentials,
            width=20
        ).grid(row=2, column=0, columnspan=2, pady=5)

        tk.Frame(self.root, height=1, bg="gray").grid(row=3, column=0, columnspan=2, sticky="ew", padx=5)

        # 2. Action buttons (row 4)
        btn_frame = tk.Frame(self.root)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=5)

        ttk.Button(
            btn_frame,
            text="Derslerimi Getir (API)",
            command=self.open_course_selector,
            width=22,
            style="Green.TButton"
        ).pack(pady=2)

        ttk.Button(
            btn_frame,
            text="Şimdi Derse Gir",
            command=self.handle_manual_join,
            width=22,
            style="Blue.TButton"
        ).pack(pady=2)

        ttk.Button(
            btn_frame,
            text="Seçili Dersi Sil",
            command=self.delete_selected_lesson,
            width=22
        ).pack(pady=2)

        ttk.Button(
            btn_frame,
            text="Ayarlar (Webhook)",
            command=self.open_settings_window,
            width=22
        ).pack(pady=2)

        # 3. Status indicator (row 5)
        self.status_label = tk.Label(self.root, text="● Scheduler aktif", anchor="w")
        self.status_label.grid(row=5, column=0, columnspan=2, padx=10, pady=(8, 2), sticky="ew")

        # 4. Lesson listbox (rows 6 & 7)
        tk.Label(self.root, text="Kayıtlı Dersler:").grid(row=6, column=0, columnspan=2, pady=(10, 0))
        self.lesson_listbox = tk.Listbox(self.root, width=42, height=10)
        self.lesson_listbox.grid(row=7, column=0, columnspan=2, padx=5, pady=5)
        self.lesson_mapping: List[Tuple[str, int]] = []

        # 5. Version label (row 8)
        tk.Label(
            self.root,
            text=f"{APP_VERSION} | Selenium-free, Hafif & Hızlı",
            fg="gray",
            font=("Arial", 8)
        ).grid(row=8, column=0, columnspan=2)

    # ── Credentials ──────────────────────────────────────────────────────────

    def _load_saved_credentials(self) -> None:
        username, password, _ = load_credentials()
        if username:
            self.username_entry.delete(0, tk.END)
            self.username_entry.insert(0, username)
        if password:
            self.password_entry.delete(0, tk.END)
            self.password_entry.insert(0, password)

        if username and password:
            self._get_or_create_session(username, password)

    def save_user_credentials(self) -> None:
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            self.show_warning("Uyarı", "Kullanıcı adı ve şifre boş olamaz!")
            return

        save_credentials(username=username, password=password)
        session = self._get_or_create_session(username, password)
        session.reset_auth_lockout()
        self.scheduler.reset_auth_lockout()
        self.show_info("Bilgi", "Kullanıcı bilgileri kaydedildi ve hesap koruması sıfırlandı!")
        logger.info(f"Credentials saved and lockout reset for: {username}")

    def _get_or_create_session(self, username: str, password: str) -> YTUSessionManager:
        if self.session_manager is None:
            self.session_manager = YTUSessionManager(username, password)
        else:
            self.session_manager.update_credentials(username, password)
        return self.session_manager

    # ── Settings Window ──────────────────────────────────────────────────────

    def open_settings_window(self) -> None:
        current_url = load_webhook_url()

        settings_window = tk.Toplevel(self.root)
        settings_window.title("Ayarlar - Discord Webhook")
        settings_window.geometry("500x200")
        settings_window.transient(self.root)
        settings_window.grab_set()

        tk.Label(
            settings_window,
            text="Discord Webhook Ayarları",
            font=("Arial", 12, "bold")
        ).pack(pady=10)

        tk.Label(
            settings_window,
            text="Ders katılım bildirimlerini Discord'a göndermek için webhook URL'i girin.",
            wraplength=450
        ).pack(pady=5)

        url_frame = tk.Frame(settings_window)
        url_frame.pack(fill="x", padx=20, pady=10)

        tk.Label(url_frame, text="Webhook URL:").pack(side="left")
        url_entry = tk.Entry(url_frame, width=50)
        url_entry.pack(side="left", padx=10, fill="x", expand=True)
        url_entry.insert(0, current_url)

        btn_frame = tk.Frame(settings_window)
        btn_frame.pack(pady=15)

        def on_test():
            url = url_entry.get().strip()
            if not url:
                messagebox.showwarning("Uyarı", "Webhook URL'i giriniz!")
                return
            if test_webhook(url):
                messagebox.showinfo("Başarılı", "Test bildirimi gönderildi!\nDiscord kanalınızı kontrol edin.")
            else:
                messagebox.showerror("Hata", "Webhook testi başarısız.\nURL'i kontrol edin.")

        def on_save():
            url = url_entry.get().strip()
            save_webhook_url(url)
            messagebox.showinfo("Bilgi", "Webhook ayarları kaydedildi!")
            settings_window.destroy()

        def on_clear():
            url_entry.delete(0, tk.END)
            save_webhook_url("")
            messagebox.showinfo("Bilgi", "Webhook devre dışı bırakıldı.")

        ttk.Button(btn_frame, text="Test Et", command=on_test, width=12).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Kaydet", command=on_save, width=12).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Temizle", command=on_clear, width=12).pack(side="left", padx=5)

    # ── Lesson Management ────────────────────────────────────────────────────

    def refresh_lesson_list(self) -> None:
        self.lesson_listbox.delete(0, tk.END)
        self.lesson_mapping = []
        schedule = load_schedule()

        for day in DAYS_CANONICAL:
            lessons = schedule.get(day, [])
            display_day = get_day_display_name(day)
            for idx, lesson in enumerate(lessons):
                text = f"{display_day}: {lesson['hour']} - {lesson.get('desc', '')}"
                self.lesson_listbox.insert(tk.END, text)
                self.lesson_mapping.append((day, idx))

        if hasattr(self, "_update_status"):
            self._update_status()

    def _update_status(self) -> None:
        """Periodic status indicator update running on Tk main thread."""
        if self._status_after_id:
            try:
                self.root.after_cancel(self._status_after_id)
            except Exception:
                pass
            self._status_after_id = None

        try:
            is_active = bool(self.scheduler and self.scheduler.is_running)
            status_prefix = "● Scheduler aktif" if is_active else "○ Scheduler durdu"

            next_lesson = self.scheduler.get_next_lesson() if self.scheduler else None
            if not next_lesson:
                status_text = f"{status_prefix} · Program boş — 'Derslerimi Getir' ile ekle"
            else:
                desc = next_lesson.get("desc", "")
                hour = next_lesson.get("hour", "")
                day_label = next_lesson.get("day_label", "")
                is_now = next_lesson.get("is_now", False)
                delta_sec = next_lesson.get("delta_seconds", 0)

                if is_now:
                    detail = f"Devam ediyor: {desc} · {hour}"
                else:
                    time_str = format_delta_time(delta_sec)
                    if day_label == "bugün":
                        detail = f"Sıradaki: {desc} · {hour} ({time_str})"
                    elif day_label == "yarın":
                        detail = f"Sıradaki: {desc} · yarın {hour} ({time_str})"
                    else:
                        detail = f"Sıradaki: {desc} · {day_label} {hour} ({time_str})"

                status_text = f"{status_prefix} · {detail}"

            if hasattr(self, "status_label"):
                self.status_label.config(text=status_text)
        except Exception as e:
            logger.debug(f"Status update error: {e}")
        finally:
            self._status_after_id = self.root.after(30000, self._update_status)

    def delete_selected_lesson(self) -> None:
        selected = self.lesson_listbox.curselection()
        if not selected:
            self.show_warning("Uyarı", "Silinecek dersi seçiniz!")
            return

        day, idx = self.lesson_mapping[selected[0]]
        deleted = store_delete_lesson(day, idx)
        if deleted:
            self.show_info("Bilgi", "Ders silindi!")
            self.refresh_lesson_list()
        else:
            self.show_error("Hata", "Ders bulunamadı veya silinemedi!")

    # ── Course Selector (LMS API) ────────────────────────────────────────────

    def open_course_selector(self) -> None:
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            self.show_warning("Uyarı", "Önce kullanıcı bilgilerini girin!")
            return

        session = self._get_or_create_session(username, password)

        # Progress window on main thread
        progress_window = tk.Toplevel(self.root)
        progress_window.title("Dersler Yükleniyor...")
        progress_window.geometry("300x100")
        progress_window.transient(self.root)
        progress_window.grab_set()

        progress_label = tk.Label(progress_window, text="Dersler API'den çekiliyor...\nLütfen bekleyin.")
        progress_label.pack(expand=True)

        def fetch_worker():
            try:
                courses = session.get_courses_safe()

                def on_done():
                    progress_window.destroy()
                    if not courses:
                        messagebox.showwarning(
                            "Uyarı",
                            "Hiç ders bulunamadı!\n\nKullanıcı adı ve şifrenizi kontrol edin."
                        )
                        return
                    self._show_course_selection_dialog(courses)

                self.ui_call(on_done)

            except Exception as e:
                def on_fail():
                    progress_window.destroy()
                    messagebox.showerror("Hata", f"Dersler çekilemedi:\n{e}")

                self.ui_call(on_fail)

        threading.Thread(target=fetch_worker, daemon=True).start()

    def _show_course_selection_dialog(self, courses: List[Dict]) -> None:
        selector_window = tk.Toplevel(self.root)
        selector_window.title("Ders Seçimi - YILDIZ")
        selector_window.geometry("700x450")
        selector_window.transient(self.root)
        selector_window.grab_set()

        tk.Label(
            selector_window,
            text="Otomatik katılmak istediğiniz dersleri seçin:",
            font=("Arial", 11, "bold")
        ).pack(pady=10)

        # Scrollable container
        canvas = tk.Canvas(selector_window)
        scrollbar = tk.Scrollbar(selector_window, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True, padx=10)
        scrollbar.pack(side="right", fill="y")

        course_vars: Dict[str, Tuple[tk.BooleanVar, Dict]] = {}

        for course in courses:
            var = tk.BooleanVar(value=False)
            frame = tk.Frame(scrollable_frame)
            frame.pack(fill="x", pady=2)

            cb = tk.Checkbutton(frame, variable=var, anchor="w")
            cb.pack(side="left")

            label_text = course['name']
            if course.get('code'):
                label_text = f"[{course['code']}] {label_text}"
            if course.get('instructor'):
                label_text += f" ({course['instructor']})"

            tk.Label(frame, text=label_text, anchor="w", wraplength=550).pack(side="left", fill="x")
            course_vars[course['no']] = (var, course)

        btn_frame = tk.Frame(selector_window)
        btn_frame.pack(pady=10)

        def on_add_selected():
            selected = [data for var, data in course_vars.values() if var.get()]
            if not selected:
                messagebox.showwarning("Uyarı", "Hiç ders seçilmedi!")
                return

            selector_window.destroy()
            self._fetch_and_add_schedules_async(selected)

        ttk.Button(btn_frame, text="Seçilenleri Ekle", command=on_add_selected, width=15).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="İptal", command=selector_window.destroy, width=15).pack(side="left", padx=5)

    def _fetch_and_add_schedules_async(self, selected_courses: List[Dict]) -> None:
        """
        Background worker to fetch schedules for selected courses
        without blocking the UI.
        """
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        session = self._get_or_create_session(username, password)

        # Show modal progress
        progress_dialog = tk.Toplevel(self.root)
        progress_dialog.title("Program Alınıyor...")
        progress_dialog.geometry("350x120")
        progress_dialog.transient(self.root)
        progress_dialog.grab_set()

        status_label = tk.Label(progress_dialog, text="Ders saatleri API'den alınıyor...")
        status_label.pack(expand=True, pady=10)

        def worker():
            added_count = 0
            schedule_dict = load_schedule()

            for course_data in selected_courses:
                prog_no = course_data['no']
                schedules = session.get_course_schedule_safe(prog_no)

                if schedules:
                    for s in schedules:
                        day = s['day']
                        hour = s['start_time']
                        # Avoid duplicates
                        existing = schedule_dict.get(day, [])
                        if any(l['hour'] == hour and l.get('desc') == course_data['name'] for l in existing):
                            continue

                        schedule_dict.setdefault(day, []).append({
                            "hour": hour,
                            "desc": course_data['name'],
                            "program_no": prog_no
                        })
                        added_count += 1
                else:
                    # If schedule not found, add default slot or allow manual edit
                    day = "Monday"
                    schedule_dict.setdefault(day, []).append({
                        "hour": "09:00",
                        "desc": course_data['name'],
                        "program_no": prog_no
                    })
                    added_count += 1

            save_schedule(schedule_dict)

            def on_finish():
                progress_dialog.destroy()
                self.refresh_lesson_list()
                messagebox.showinfo("Başarılı", f"{added_count} ders saati başarıyla eklendi!")

            self.ui_call(on_finish)

        threading.Thread(target=worker, daemon=True).start()

    # ── Automation Logic ─────────────────────────────────────────────────────

    def get_current_lesson(self) -> Optional[Tuple[str, str, Optional[str]]]:
        """
        Finds scheduled lesson within manual join window
        (15m before to 60m after).
        Returns (course_name, hour, program_no) or None.
        Python 3.8 compatible return typing.
        """
        now = datetime.datetime.now()
        current_day = get_current_day(now)
        schedule = load_schedule()

        if current_day not in schedule:
            return None

        best_match = None
        min_diff = float('inf')

        for lesson in schedule[current_day]:
            try:
                h, m = map(int, lesson["hour"].split(":"))
                lesson_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            except Exception:
                continue

            time_until_lesson = (lesson_time - now).total_seconds()

            # Window: 60m after to 15m before class starts
            if -MANUAL_JOIN_AFTER <= time_until_lesson <= MANUAL_JOIN_BEFORE:
                abs_diff = abs(time_until_lesson)
                if abs_diff < min_diff:
                    min_diff = abs_diff
                    best_match = (
                        lesson.get('desc', ''),
                        lesson.get('hour', ''),
                        lesson.get('program_no')
                    )

        return best_match

    def handle_manual_join(self) -> None:
        """Handler for 'Şimdi Derse Gir' button."""
        lesson = self.get_current_lesson()
        if lesson is None:
            self.show_warning(
                "Uyarı",
                "Şu an için uygun bir ders bulunamadı!\n\n"
                "Tolerans Penceresi: Dersten 15 dk önce ~ Dersten 60 dk sonra.\n"
                "Lütfen ders programınızı kontrol edin."
            )
            return

        course_name, hour, program_no = lesson
        logger.info(f"[MANUAL] Triggering join for: {course_name} ({hour}, program_no: {program_no})")

        threading.Thread(
            target=self.run_automation_flow,
            args=(course_name, program_no, hour, True),
            daemon=True
        ).start()

    def run_automation_flow(
        self,
        course_name: str,
        program_no: Optional[str] = None,
        hour: Optional[str] = None,
        is_manual: bool = False
    ) -> JoinResult:
        """
        Core join execution flow.
        Acquires automation lock to prevent duplicate concurrent launches.
        Returns JoinResult with status.
        """
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        webhook_url = load_webhook_url()

        if not username or not password:
            if is_manual:
                self.show_warning("Uyarı", "Lütfen önce kullanıcı bilgilerinizi kaydedin!")
            return JoinResult(success=False, status="AUTH_FAILED", message="Kullanıcı bilgileri eksik")

        with self._automation_lock:
            try:
                session = self._get_or_create_session(username, password)

                if not session.ensure_logged_in():
                    if is_manual:
                        self.show_error("Hata", "Giriş başarısız! Kullanıcı adı veya şifrenizi kontrol edin.")
                        notify_auth_failed(webhook_url, course_name, hour)
                    return JoinResult(success=False, status="AUTH_FAILED", message="Giriş başarısız")

                # Extract Zoom link
                zoom_url = session.get_zoom_link_safe(
                    course_name=course_name,
                    program_no=program_no
                )

                if not zoom_url:
                    if is_manual:
                        self.show_warning(
                            "Uyarı",
                            "Aktif ders bulunamadı veya Zoom linki henüz açılmadı.\n\n"
                            "Hocanın toplantıyı başlattığından emin olun."
                        )
                        notify_no_link_found(webhook_url, course_name)
                    # For automated scheduler retry: SILENT! Zero Discord spam!
                    return JoinResult(success=False, status="LINK_NOT_FOUND", message="Link henüz açılmadı")

                # Launch Zoom via protocol handler / browser
                opened = open_zoom_link(zoom_url)
                if opened:
                    if hasattr(self, "scheduler") and self.scheduler:
                        try:
                            self.scheduler.record_joined_lesson({
                                "desc": course_name,
                                "program_no": program_no,
                                "hour": hour or "",
                            })
                        except Exception as e:
                            logger.warning(f"Could not record joined lesson in scheduler history: {e}")

                    if is_manual:
                        self.show_info(
                            "Başarılı",
                            f"Zoom açıldı!\n\nDers: {course_name}\nLütfen Zoom ekranını kontrol edin."
                        )
                    notify_lesson_joined(webhook_url, course_name, hour)
                    return JoinResult(success=True, status="SUCCESS", message="Zoom açıldı")
                else:
                    if is_manual:
                        self.show_error("Hata", "Zoom linki açılamadı. Zoom yüklü mü?")
                    notify_lesson_failed(webhook_url, course_name, "Zoom açılamadı", hour)
                    return JoinResult(success=False, status="ERROR", message="Zoom açılamadı")

            except Exception as e:
                logger.error(f"Automation execution failed: {e}", exc_info=True)
                if is_manual:
                    self.show_error("Hata", f"Otomasyon hatası:\n{e}")
                notify_lesson_failed(webhook_url, course_name, str(e), hour)
                return JoinResult(success=False, status="ERROR", message=str(e))

    def _scheduler_join_callback(self, lesson: Dict) -> JoinResult:
        """Invoked by background LessonScheduler."""
        course_name = lesson.get("desc", "")
        program_no = lesson.get("program_no")
        hour = lesson.get("hour", "")
        return self.run_automation_flow(
            course_name=course_name,
            program_no=program_no,
            hour=hour,
            is_manual=False
        )

    def _on_notify_waiting(self, course_name: str, hour: str) -> None:
        webhook_url = load_webhook_url()
        if webhook_url:
            notify_waiting_for_link(webhook_url, course_name, hour)

    def _on_notify_still_waiting(self, course_name: str, hour: str, minutes_elapsed: int) -> None:
        webhook_url = load_webhook_url()
        if webhook_url:
            notify_still_waiting_for_link(webhook_url, course_name, hour, minutes_elapsed)

    def _on_notify_missed(self, course_name: str, hour: str, reason: str) -> None:
        webhook_url = load_webhook_url()
        if webhook_url:
            notify_lesson_missed(webhook_url, course_name, hour, reason)

    def _on_notify_auth_failed(self, course_name: str, hour: str) -> None:
        webhook_url = load_webhook_url()
        if webhook_url:
            notify_auth_failed(webhook_url, course_name, hour)

    def _on_auth_failed_alert(self) -> None:
        self.show_error(
            "Giriş Başarısız (Hesap Koruması)",
            "Giriş bilgileri doğrulanamadı.\n\n"
            "Üniversite hesabınızın kilitlenmesini önlemek için otomatik denemeler durduruldu.\n"
            "Lütfen bilgilerinizi kontrol edip 'Bilgileri Kaydet' butonuyla güncelleyin."
        )

    def on_close(self) -> None:
        """Gracefully shuts down scheduler, cancels status timers, and closes UI."""
        logger.info("Application closing...")
        if self._status_after_id:
            try:
                self.root.after_cancel(self._status_after_id)
            except Exception:
                pass
            self._status_after_id = None
        try:
            self.scheduler.stop()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    setup_logging()
    root = tk.Tk()
    app = AutomationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
