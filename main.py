"""
YILDIZ Ders Otomasyonu - v1.0
Selenium-free, Hafif & Hızlı
"""
import tkinter as tk
from tkinter import messagebox, ttk
import json
import threading
import time
import datetime
import shelve
import os
import sys
import logging

# ── Core modules (Pure Terminal) ─────────────────────────────────────────────
from ytu_client import YTUSessionManager, YTUClientException
from zoom_launcher import open_zoom_link
from config import SCHEDULE_FILE, USER_FILE, CHECK_INTERVAL, JOIN_TOLERANCE, MANUAL_JOIN_BEFORE, MANUAL_JOIN_AFTER
from discord_notifier import (
    notify_lesson_joined,
    notify_lesson_failed,
    notify_scheduler_triggered,
    notify_no_link_found,
    test_webhook
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('automation.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ── Global state ─────────────────────────────────────────────────────────────
lesson_mapping = []  # [(day, index), ...]
joined_lessons = set()  # Prevent duplicate joins
current_username = ""
current_password = ""
discord_webhook_url = ""  # Discord webhook URL for notifications
ytu_session = None  # YTU session manager (lazy init)


# ── Credential Management ────────────────────────────────────────────────────

def save_credentials():
    """Save username and password to shelve storage"""
    global current_username, current_password
    current_username = username_entry.get().strip()
    current_password = password_entry.get().strip()

    if not current_username or not current_password:
        messagebox.showwarning("Uyarı", "Kullanıcı adı ve şifre boş olamaz!")
        return

    with shelve.open(str(USER_FILE)) as db:
        db["username"] = current_username
        db["password"] = current_password

    messagebox.showinfo("Bilgi", "Kullanıcı bilgileri kaydedildi!")
    logger.info(f"Credentials saved for: {current_username}")


def load_credentials():
    """Load saved credentials and populate GUI fields"""
    global current_username, current_password
    try:
        with shelve.open(str(USER_FILE)) as db:
            current_username = db.get("username", "")
            current_password = db.get("password", "")
            username_entry.insert(0, current_username)
            password_entry.insert(0, current_password)
        if current_username:
            logger.info(f"Loaded credentials for: {current_username}")
    except Exception as e:
        logger.warning(f"Could not load credentials: {e}")


# ── Discord Webhook Management ────────────────────────────────────────────────

def load_webhook_url():
    """Load saved Discord webhook URL"""
    global discord_webhook_url
    try:
        with shelve.open(str(USER_FILE)) as db:
            discord_webhook_url = db.get("discord_webhook_url", "")
        if discord_webhook_url:
            logger.info("Discord webhook URL loaded")
    except Exception as e:
        logger.warning(f"Could not load webhook URL: {e}")


def save_webhook_url(url: str):
    """Save Discord webhook URL"""
    global discord_webhook_url
    discord_webhook_url = url.strip()
    with shelve.open(str(USER_FILE)) as db:
        db["discord_webhook_url"] = discord_webhook_url
    logger.info("Discord webhook URL saved")


def open_settings_window():
    """Open settings window for Discord webhook configuration"""
    global discord_webhook_url

    settings_window = tk.Toplevel(root)
    settings_window.title("Ayarlar - Discord Webhook")
    settings_window.geometry("500x200")
    settings_window.transient(root)
    settings_window.grab_set()

    # Discord webhook section
    tk.Label(
        settings_window,
        text="Discord Webhook Ayarlari",
        font=("Arial", 12, "bold")
    ).pack(pady=10)

    tk.Label(
        settings_window,
        text="Ders katilim bildirimlerini Discord'a gondermek icin webhook URL'i girin.",
        wraplength=450
    ).pack(pady=5)

    # URL entry
    url_frame = tk.Frame(settings_window)
    url_frame.pack(fill="x", padx=20, pady=10)

    tk.Label(url_frame, text="Webhook URL:").pack(side="left")
    url_entry = tk.Entry(url_frame, width=50)
    url_entry.pack(side="left", padx=10, fill="x", expand=True)
    url_entry.insert(0, discord_webhook_url)

    # Buttons
    btn_frame = tk.Frame(settings_window)
    btn_frame.pack(pady=15)

    def on_test():
        url = url_entry.get().strip()
        if not url:
            messagebox.showwarning("Uyari", "Webhook URL'i giriniz!")
            return

        if test_webhook(url):
            messagebox.showinfo("Basarili", "Test bildirimi gonderildi!\nDiscord kanalinizi kontrol edin.")
        else:
            messagebox.showerror("Hata", "Webhook testi basarisiz.\nURL'i kontrol edin.")

    def on_save():
        url = url_entry.get().strip()
        save_webhook_url(url)
        messagebox.showinfo("Bilgi", "Webhook ayarlari kaydedildi!")
        settings_window.destroy()

    def on_clear():
        url_entry.delete(0, tk.END)
        save_webhook_url("")
        messagebox.showinfo("Bilgi", "Webhook devre disi birakildi.")

    ttk.Button(btn_frame, text="Test Et", command=on_test, width=12).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="Kaydet", command=on_save, width=12).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="Temizle", command=on_clear, width=12).pack(side="left", padx=5)


# ── Schedule Management ──────────────────────────────────────────────────────

def load_schedule() -> dict:
    """Load schedule from JSON file"""
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_schedule(schedule: dict):
    """Save schedule to JSON file"""
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)


def update_lesson_list():
    """Update lesson listbox in GUI"""
    global lesson_mapping
    lesson_listbox.delete(0, tk.END)
    lesson_mapping = []
    schedule = load_schedule()

    for day, lessons in schedule.items():
        for idx, lesson in enumerate(lessons):
            display_text = f"{day}: {lesson['hour']} - {lesson.get('desc', '')}"
            lesson_listbox.insert(tk.END, display_text)
            lesson_mapping.append((day, idx))


def add_lesson():
    """Add new lesson to schedule"""
    day = day_var.get()
    hour = hour_entry.get().strip()
    desc = desc_entry.get().strip()

    if not hour:
        messagebox.showwarning("Uyarı", "Ders saatini giriniz!")
        return

    # Validate time format
    try:
        datetime.datetime.strptime(hour, "%H:%M")
    except ValueError:
        messagebox.showwarning("Uyarı", "Saat formatı HH:MM olmalıdır! (örn: 09:30)")
        return

    schedule = load_schedule()
    schedule.setdefault(day, []).append({"hour": hour, "desc": desc})
    save_schedule(schedule)

    messagebox.showinfo("Bilgi", f"Ders eklendi: {day} günü saat {hour}")
    logger.info(f"Lesson added: {day} {hour} - {desc}")
    update_lesson_list()


def delete_lesson():
    """Delete selected lesson from schedule"""
    selected = lesson_listbox.curselection()
    if not selected:
        messagebox.showwarning("Uyarı", "Silinecek dersi seçiniz!")
        return

    day, lesson_index = lesson_mapping[selected[0]]
    schedule = load_schedule()

    if day in schedule and lesson_index < len(schedule[day]):
        deleted_lesson = schedule[day][lesson_index]
        del schedule[day][lesson_index]
        if not schedule[day]:
            del schedule[day]
        save_schedule(schedule)

        messagebox.showinfo("Bilgi", "Ders silindi!")
        logger.info(f"Lesson deleted: {day} {deleted_lesson['hour']}")
        update_lesson_list()
    else:
        messagebox.showerror("Hata", "Ders bulunamadı!")


# ── Course Selector (API-based) ─────────────────────────────────────────────

def open_course_selector():
    """
    Login yap ve API'den dersleri çekip
    checkbox listesi olarak kullanıcıya sun
    """
    global ytu_session, current_username, current_password

    username = current_username or username_entry.get().strip()
    password = current_password or password_entry.get().strip()

    if not username or not password:
        messagebox.showwarning("Uyarı", "Önce kullanıcı bilgilerini girin!")
        return

    # Progress dialog
    progress_window = tk.Toplevel(root)
    progress_window.title("Dersler Yükleniyor...")
    progress_window.geometry("300x100")
    progress_window.transient(root)
    progress_window.grab_set()

    progress_label = tk.Label(progress_window, text="Dersler API'den çekiliyor...\nLütfen bekleyin.")
    progress_label.pack(expand=True)

    root.update()

    def fetch_courses():
        global ytu_session

        try:
            # Session manager'ı initialize et veya mevcut olanı kullan
            if not ytu_session or ytu_session.username != username:
                ytu_session = YTUSessionManager(username, password)

            courses = ytu_session.get_courses_safe()

            # Progress'i kapat ve seçim penceresini aç
            progress_window.destroy()

            if not courses:
                messagebox.showwarning("Uyarı", "Hiç ders bulunamadı!\n\nLogin bilgilerini kontrol edin.")
                return

            show_course_selection_dialog(courses)

        except Exception as e:
            progress_window.destroy()
            messagebox.showerror("Hata", f"Dersler çekilemedi:\n{e}")
            logger.error(f"Course fetch error: {e}")

    # Arka planda çek (UI donmasın)
    threading.Thread(target=fetch_courses, daemon=True).start()


def show_course_selection_dialog(courses: list):
    """
    Checkbox listesi ile ders seçim penceresi
    """
    selector_window = tk.Toplevel(root)
    selector_window.title("Ders Seçimi - YILDIZ")
    selector_window.geometry("900x400")
    selector_window.transient(root)

    # Başlık
    tk.Label(
        selector_window,
        text="Otomatik katılmak istediğiniz dersleri seçin:",
        font=("Arial", 11, "bold")
    ).pack(pady=10)

    # Scrollable frame
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

    # Checkbox'lar
    course_vars = {}  # {program_no: (BooleanVar, course_data)}

    for course in courses:
        var = tk.BooleanVar(value=False)

        # Checkbox + Label
        frame = tk.Frame(scrollable_frame)
        frame.pack(fill="x", pady=2)

        cb = tk.Checkbutton(
            frame,
            variable=var,
            anchor="w"
        )
        cb.pack(side="left")

        label_text = f"{course['name']}"
        if course.get('code'):
            label_text = f"[{course['code']}] {label_text}"

        tk.Label(
            frame,
            text=label_text,
            anchor="w",
            wraplength=400
        ).pack(side="left", fill="x")

        course_vars[course['no']] = (var, course)

    # Saat bilgisi için frame
    time_frame = tk.LabelFrame(selector_window, text="Ders Saati Bilgisi", padx=10, pady=5)
    time_frame.pack(fill="x", padx=10, pady=10)

    tk.Label(time_frame, text="Saatler API'den otomatik çekilecek.").pack()
    tk.Label(time_frame, text="Bulunamazsa manuel giriş istenir.").pack()

    # Butonlar
    button_frame = tk.Frame(selector_window)
    button_frame.pack(pady=10)

    def on_add_selected():
        """Seçili dersleri schedule'a ekle - önce API'den saat çek"""
        global ytu_session

        selected = [(no, data) for no, (var, data) in course_vars.items() if var.get()]

        if not selected:
            messagebox.showwarning("Uyarı", "Hiç ders seçilmedi!")
            return

        # Her seçili ders için saatleri çek ve ekle
        added_count = 0
        manual_count = 0

        for program_no, course_data in selected:
            # API'den ders saatlerini çek
            schedules = []
            if ytu_session:
                schedules = ytu_session.get_course_schedule_safe(program_no)

            if schedules:
                # Saatler bulundu - otomatik ekle
                schedule = load_schedule()
                for sched in schedules:
                    day = sched['day']
                    hour = sched['start_time']

                    # Aynı ders aynı saatte varsa ekleme
                    existing = schedule.get(day, [])
                    if any(l['hour'] == hour and l.get('desc', '') == course_data['name'] for l in existing):
                        continue

                    schedule.setdefault(day, []).append({
                        "hour": hour,
                        "desc": course_data['name'],
                        "program_no": program_no
                    })
                    logger.info(f"Auto-added: {day} {hour} - {course_data['name']}")
                    added_count += 1
                save_schedule(schedule)
            else:
                # Saatler bulunamadı - manuel giriş
                add_course_with_time_dialog(course_data)
                manual_count += 1

        selector_window.destroy()
        update_lesson_list()

        # Sonucu göster
        if added_count > 0 and manual_count == 0:
            messagebox.showinfo("Başarılı", f"{added_count} ders saati otomatik eklendi!")
        elif added_count > 0 and manual_count > 0:
            messagebox.showinfo("Bilgi", f"{added_count} ders otomatik, {manual_count} ders manuel eklendi.")

    ttk.Button(
        button_frame,
        text="Seçilenleri Ekle",
        command=on_add_selected,
        width=15
    ).pack(side="left", padx=5)

    ttk.Button(
        button_frame,
        text="İptal",
        command=selector_window.destroy,
        width=15
    ).pack(side="left", padx=5)


def add_course_with_time_dialog(course_data: dict):
    """
    Tek bir ders için gün ve saat seçimi dialog'u
    """
    dialog = tk.Toplevel(root)
    dialog.title(f"Saat Seçimi: {course_data['name'][:30]}...")
    dialog.geometry("350x200")
    dialog.transient(root)
    dialog.grab_set()

    tk.Label(
        dialog,
        text=f"Ders: {course_data['name']}",
        font=("Arial", 10, "bold"),
        wraplength=320
    ).pack(pady=10)

    # Gün seçimi
    day_frame = tk.Frame(dialog)
    day_frame.pack(fill="x", padx=20, pady=5)

    tk.Label(day_frame, text="Gün:").pack(side="left")
    day_var_dialog = tk.StringVar(value="Monday")
    day_menu = tk.OptionMenu(
        day_frame, day_var_dialog,
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    )
    day_menu.pack(side="left", padx=10)

    # Saat girişi
    time_frame = tk.Frame(dialog)
    time_frame.pack(fill="x", padx=20, pady=5)

    tk.Label(time_frame, text="Saat (HH:MM):").pack(side="left")
    time_entry = tk.Entry(time_frame, width=10)
    time_entry.pack(side="left", padx=10)
    time_entry.insert(0, "09:00")

    result = {'saved': False}

    def on_save():
        hour = time_entry.get().strip()
        day = day_var_dialog.get()

        # Saat format kontrolü
        try:
            datetime.datetime.strptime(hour, "%H:%M")
        except ValueError:
            messagebox.showwarning("Uyarı", "Saat formatı HH:MM olmalı!")
            return

        # Schedule'a ekle
        schedule = load_schedule()
        schedule.setdefault(day, []).append({
            "hour": hour,
            "desc": course_data['name'],
            "program_no": course_data['no']
        })
        save_schedule(schedule)

        logger.info(f"Course added: {day} {hour} - {course_data['name']}")
        result['saved'] = True
        dialog.destroy()

    def on_skip():
        dialog.destroy()

    button_frame = tk.Frame(dialog)
    button_frame.pack(pady=15)

    tk.Button(button_frame, text="Kaydet", command=on_save, width=10).pack(side="left", padx=5)
    tk.Button(button_frame, text="Atla", command=on_skip, width=10).pack(side="left", padx=5)

    dialog.wait_window()
    return result['saved']


# ── Automation Core (Pure Terminal - no Selenium!) ───────────────────────────


def get_current_lesson() -> tuple[str, str] | None:
    """
    Find the current or upcoming lesson within tolerance.

    Returns:
        tuple (course_name, hour) if a lesson is found within tolerance:
        - 15 minutes before class start
        - 60 minutes after class starts
        None if no matching lesson
    """
    now = datetime.datetime.now()
    current_day = now.strftime("%A")
    schedule = load_schedule()

    if current_day not in schedule:
        return None

    best_match = None
    min_diff = float('inf')

    for lesson in schedule[current_day]:
        lesson_time = datetime.datetime.strptime(lesson["hour"], "%H:%M").replace(
            year=now.year, month=now.month, day=now.day
        )
        # Time until lesson starts (negative = lesson in the future)
        time_until_lesson = (lesson_time - now).total_seconds()

        # Valid window: 15 min before to 60 min after class starts
        if -MANUAL_JOIN_BEFORE <= time_until_lesson <= MANUAL_JOIN_AFTER:
            # Use absolute time in window for best match
            abs_diff = abs(time_until_lesson)
            if abs_diff < min_diff:
                min_diff = abs_diff
                best_match = (lesson.get('desc', ''), lesson['hour'])

    return best_match


def handle_manual_join():
    """
    Handler for "Şimdi Derse Gir" button.
    Checks schedule before joining to prevent wrong class entry.
    Tolerance: 15 minutes before to 60 minutes after class starts.
    """
    lesson = get_current_lesson()

    if lesson is None:
        messagebox.showwarning(
            "Uyarı",
            "Uygun ders bulunamadı!\n\n"
            "Tolerans: Dersten 15 dakika önce ~ Dersten 60 dakika sonrası\n"
            "Lütfen ders programınızı kontrol edin."
        )
        logger.warning("[MANUAL] No scheduled lesson found within tolerance")
        return

    course_name, hour = lesson
    logger.info(f"[MANUAL] Found scheduled lesson: {course_name} at {hour}")

    # Start automation with found course name
    threading.Thread(target=run_automation, args=(course_name,), daemon=True).start()


def run_automation(course_name: str = None):
    """
    Pure Terminal automation - Join class without Selenium

    Args:
        course_name: Optional course name to filter (from scheduler)
                    If None, will join first available class

    Flow:
    1. Ensure logged into YTU Online (uses cached session if available)
    2. Call AttendLessonProgram API to get Zoom link
    3. Launch Zoom using platform-specific protocol handler
    """
    global ytu_session

    username = current_username
    password = current_password

    if not username or not password:
        messagebox.showwarning("Uyarı", "Lütfen önce kullanıcı bilgilerini girin ve kaydedin!")
        return

    logger.info("=== Automation started ===")
    if course_name:
        logger.info(f"Target course: {course_name}")

    try:
        # Initialize session manager (lazy init)
        if not ytu_session or ytu_session.username != username:
            logger.info("Initializing YTU session manager...")
            ytu_session = YTUSessionManager(username, password)

        # Step 1: Ensure logged in
        logger.info("Step 1: Checking login status...")
        if not ytu_session.ensure_logged_in():
            messagebox.showerror("Hata", "Login başarısız! Kullanıcı adı/şifre kontrol edin.")
            logger.error("Login failed")
            return

        # Step 2: Get Zoom link (with optional course filter)
        logger.info("Step 2: Getting Zoom link from API...")
        zoom_url = ytu_session.get_zoom_link_safe(course_name)

        if not zoom_url:
            messagebox.showwarning("Uyarı", "Aktif ders bulunamadı veya Zoom linki çıkarılamadı.\n\nDers saatinde olduğunuzdan emin olun.")
            logger.warning("No Zoom link found")
            notify_no_link_found(discord_webhook_url, course_name)
            return

        # Step 3: Open Zoom
        logger.info(f"Step 3: Opening Zoom... {zoom_url[:60]}...")
        success = open_zoom_link(zoom_url)

        if success:
            messagebox.showinfo("Başarılı", "Zoom uygulaması açıldı!\n\nLütfen Zoom'da 'Join' butonuna tıklayın.")
            logger.info("✓ Automation completed successfully")
            notify_lesson_joined(discord_webhook_url, course_name or "Bilinmeyen Ders")
        else:
            messagebox.showerror("Hata", "Zoom linki açılamadı. Zoom uygulaması yüklü mü?")
            logger.error("Failed to open Zoom")
            notify_lesson_failed(discord_webhook_url, course_name or "Bilinmeyen Ders", "Zoom acilamadi")

    except YTUClientException as e:
        messagebox.showerror("Hata", f"Otomasyon başarısız:\n{e}")
        logger.error(f"Automation error: {e}")
        notify_lesson_failed(discord_webhook_url, course_name or "Bilinmeyen Ders", str(e))
    except Exception as e:
        messagebox.showerror("Hata", f"Beklenmeyen hata:\n{e}")
        logger.error(f"Unexpected error: {e}", exc_info=True)
        notify_lesson_failed(discord_webhook_url, course_name or "Bilinmeyen Ders", str(e))


# ── Scheduler ────────────────────────────────────────────────────────────────

def check_schedule():
    """
    Background scheduler - monitors schedule and triggers automation

    Runs in daemon thread, checks every CHECK_INTERVAL seconds
    """
    logger.info(f"Scheduler started (check interval: {CHECK_INTERVAL}s, tolerance: ±{JOIN_TOLERANCE}s)")

    while True:
        try:
            now = datetime.datetime.now()
            current_day = now.strftime("%A")
            schedule = load_schedule()

            if current_day in schedule:
                for lesson in schedule[current_day]:
                    lesson_key = f"{current_day}-{lesson['hour']}"

                    # Prevent duplicate joins
                    if lesson_key in joined_lessons:
                        continue

                    # Time comparison with tolerance
                    lesson_time = datetime.datetime.strptime(lesson["hour"], "%H:%M").replace(
                        year=now.year, month=now.month, day=now.day
                    )
                    diff = abs((now - lesson_time).total_seconds())

                    if diff <= JOIN_TOLERANCE:
                        course_name = lesson.get('desc', '')
                        hour = lesson['hour']
                        logger.info(f"[SCHEDULER] Time to join: {lesson_key} - {course_name} (diff: {diff:.0f}s)")
                        joined_lessons.add(lesson_key)

                        # Send Discord notification
                        notify_scheduler_triggered(discord_webhook_url, course_name, hour)

                        # Start automation in background thread with course name
                        threading.Thread(
                            target=run_automation,
                            args=(course_name,),
                            daemon=True
                        ).start()
                        break

        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)

        time.sleep(CHECK_INTERVAL)


# ── GUI Setup ────────────────────────────────────────────────────────────────

root = tk.Tk()
root.title("YILDIZ Ders Otomasyonu v1.0")
root.resizable(False, False)

# Configure ttk styles for colored buttons (macOS compatible)
style = ttk.Style()
style.configure("Green.TButton", font=("Arial", 10))
style.configure("Blue.TButton", font=("Arial", 10))

# Credentials section
tk.Label(root, text="Okul Maili:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
username_entry = tk.Entry(root, width=25)
username_entry.grid(row=0, column=1, padx=5, pady=5)

tk.Label(root, text="Şifre:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
password_entry = tk.Entry(root, show="*", width=25)
password_entry.grid(row=1, column=1, padx=5, pady=5)

tk.Button(root, text="Bilgileri Kaydet", command=save_credentials, width=20).grid(
    row=2, column=0, columnspan=2, pady=5
)

tk.Frame(root, height=1, bg="gray").grid(row=3, column=0, columnspan=2, sticky="ew", padx=5)

# Lesson management section
tk.Label(root, text="Gün:").grid(row=4, column=0, padx=5, pady=5, sticky="e")
day_var = tk.StringVar(root, value="Monday")
tk.OptionMenu(root, day_var,
              "Monday", "Tuesday", "Wednesday", "Thursday",
              "Friday", "Saturday", "Sunday").grid(row=4, column=1, padx=5, pady=5, sticky="w")

tk.Label(root, text="Saat (HH:MM):").grid(row=5, column=0, padx=5, pady=5, sticky="e")
hour_entry = tk.Entry(root, width=10)
hour_entry.grid(row=5, column=1, padx=5, pady=5, sticky="w")

tk.Label(root, text="Ders Adı:").grid(row=6, column=0, padx=5, pady=5, sticky="e")
desc_entry = tk.Entry(root, width=25)
desc_entry.grid(row=6, column=1, padx=5, pady=5)

tk.Button(root, text="Ders Ekle", command=add_lesson, width=20).grid(
    row=7, column=0, columnspan=2, pady=5
)

tk.Frame(root, height=1, bg="gray").grid(row=8, column=0, columnspan=2, sticky="ew", padx=5)

# Action buttons
btn_frame = tk.Frame(root)
btn_frame.grid(row=9, column=0, columnspan=2, pady=5)

ttk.Button(
    btn_frame,
    text="Derslerimi Getir (API)",
    command=open_course_selector,
    width=22,
    style="Green.TButton"
).pack(pady=2)

ttk.Button(
    btn_frame,
    text="Şimdi Derse Gir",
    command=handle_manual_join,
    width=22,
    style="Blue.TButton"
).pack(pady=2)

ttk.Button(btn_frame, text="Seçili Dersi Sil", command=delete_lesson, width=22).pack(pady=2)

ttk.Button(btn_frame, text="Ayarlar (Webhook)", command=open_settings_window, width=22).pack(pady=2)

# Lesson list
tk.Label(root, text="Kayıtlı Dersler:").grid(row=10, column=0, columnspan=2, pady=(10, 0))
lesson_listbox = tk.Listbox(root, width=40, height=10)
lesson_listbox.grid(row=11, column=0, columnspan=2, padx=5, pady=5)

# Version info
tk.Label(
    root,
    text="v1.0 | Selenium-free, Hafif & Hızlı",
    fg="gray",
    font=("Arial", 8)
).grid(row=12, column=0, columnspan=2)

# ── Startup ──────────────────────────────────────────────────────────────────

logger.info("=== YILDIZ Ders Otomasyonu v1.0 ===")
logger.info("Mode: Selenium-free, API-based")

load_credentials()
load_webhook_url()
update_lesson_list()

# Start scheduler thread
threading.Thread(target=check_schedule, daemon=True).start()

logger.info("Ready. Monitoring schedule...")
print("\n" + "="*50)
print("YILDIZ Ders Otomasyonu v1.0")
print("="*50)
print("✓ Selenium-free, Tarayıcı gerektirmez")
print("✓ Hafif & Hızlı")
print("✓ Zoom direkt protokol ile açılır")
print("="*50 + "\n")

root.mainloop()
