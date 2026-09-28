#!/bin/bash
# ============================================================
#  YILDIZ Ders Otomasyonu - macOS Baslatici
#  v2.0 (self-healing: venv yolu bagimliligi yok)
# ============================================================
#
# Digeri: bu betik sanal ortami (venv) "source" ile AKTIF ETMEZ.
# Instead dogrudan "$VENV_DIR/bin/python" cagirir. Boylece:
#   - Proje klasoru tasindiginda (orn. ~/Projects -> ~/Desktop/Projects)
#     venv/bin/activate icindeki eski VIRTUAL_ENV yolu calismaya devam eder.
#   - Sistemde "pip" komutu bulunmadiginda (Homebrew sadece pip3 kurar)
#     hata vermez; her zaman "python -m pip" kullanilir.
# ============================================================

set -u

VENV_DIR="venv"
REQUIREMENTS="requirements.txt"
MAIN_SCRIPT="main.py"

FORCE_RECREATE=0
INSTALL_ONLY=0

# ------------------------------------------------------------
#  Cikti yardimcilari
# ------------------------------------------------------------
info() { printf '[INFO] %s\n' "$*"; }
ok()   { printf '[OK] %s\n' "$*"; }
warn() { printf '[UYARI] %s\n' "$*"; }
err()  { printf '[HATA] %s\n' "$*"; }

pause_if_tty() {
    if [ -t 0 ]; then
        printf '\n'
        read -r -p 'Devam etmek icin Enter tusuna basin... ' _ || true
    fi
}

die() {
    err "$*"
    pause_if_tty
    exit 1
}

usage() {
    cat <<'EOF'
Kullanim: ./run_macos.command [secenekler]

Secenekler:
  --recreate-venv   Mevcut sanal ortami silip yeniden olusturur
  --install-only    Sadece bagimliliklari kurar, uygulamayi calistirmaz
  -h, --help        Bu yardim metnini gosterir
EOF
}

# ------------------------------------------------------------
#  Argumanlari isle
# ------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --recreate-venv) FORCE_RECREATE=1 ;;
        --install-only)  INSTALL_ONLY=1 ;;
        -h|--help)       usage; exit 0 ;;
        *)               usage; die "Bilinmeyen secenek: $1" ;;
    esac
    shift
done

printf '========================================\n'
printf 'YILDIZ Ders Otomasyonu v2.0\n'
printf '========================================\n\n'

# ------------------------------------------------------------
#  1) Proje klasorune gec
# ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)" \
    || die "Script konumu belirlenemedi."
cd "$SCRIPT_DIR" || die "Proje klasorune gecilemedi: $SCRIPT_DIR"

info "Proje klasoru: $SCRIPT_DIR"

# ------------------------------------------------------------
#  2) Python 3 bul
#
#     Terminal'de cift tiklandiginda shell profilleri yuklenmeyebilir;
#     bu durumda PATH'te yalnizca Apple'in sistem Python'u (/usr/bin/python3,
#     3.9.x) gorunur. Oncelikle Homebrew / python.org kurulumlarini
#     DOGRUDAN deneriz, boylece venv eski bir surumle kurulmaz.
# ------------------------------------------------------------
is_supported_python() {
    [ -x "$1" ] || return 1
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null
}

PYTHON3=""

# 2a) Bilinen kurulum yerleri (modern Python tercih edilir)
for abs in \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    "$HOME/Library/Python"/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3
do
    if is_supported_python "$abs"; then
        PYTHON3="$abs"
        break
    fi
done

# 2b) PATH uzerinden ara (yedek yontem)
if [ -z "$PYTHON3" ]; then
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9; do
        if command -v "$candidate" >/dev/null 2>&1 && is_supported_python "$candidate"; then
            PYTHON3="$candidate"
            break
        fi
    done
fi

[ -n "$PYTHON3" ] || die "Python 3.8+ bulunamadi.
Lutfen kurun:
  brew install python
veya https://www.python.org/downloads/ adresinden indirin."

ok "Python bulundu: $PYTHON3 ($("$PYTHON3" --version 2>&1))"

# tkinter (GUI icin gerekli) kontrolu
if ! "$PYTHON3" -c 'import tkinter' >/dev/null 2>&1; then
    warn "tkinter bulunamadi; arayuz acilamayabilir."
    warn "Cozum: brew install python-tk   (ardindan tekrar deneyin)"
fi

[ -f "$REQUIREMENTS" ] || die "requirements.txt bulunamadi: $SCRIPT_DIR/$REQUIREMENTS"
[ -f "$MAIN_SCRIPT" ]   || die "main.py bulunamadi: $SCRIPT_DIR/$MAIN_SCRIPT"

VENV_PY="$SCRIPT_DIR/$VENV_DIR/bin/python"

# ------------------------------------------------------------
#  4) Sanal ortami saglikli mi? Degilse yeniden olustur.
# ------------------------------------------------------------
venv_is_healthy() {
    [ -x "$VENV_PY" ] || return 1
    "$VENV_PY" -c 'import sys' >/dev/null 2>&1 || return 1
    return 0
}

remove_venv() {
    # Sadece gercekten bir sanal ortam olan klasoru sil.
    if [ -f "$VENV_DIR/pyvenv.cfg" ] || [ -f "$VENV_DIR/bin/activate" ]; then
        rm -rf "$VENV_DIR"
    else
        die "'$VENV_DIR' bir sanal ortam gibi gorunmuyor; guvenlik icin silinmedi.
Lutfen klasoru elle kontrol edin veya silin."
    fi
}

if [ "$FORCE_RECREATE" -eq 1 ] && [ -d "$VENV_DIR" ]; then
    info "'--recreate-venv' verildi; eski sanal ortam siliniyor..."
    remove_venv
fi

if [ -d "$VENV_DIR" ] && ! venv_is_healthy; then
    warn "'$VENV_DIR' bozuk veya eski konumdan kalmis."
    warn "Nedeni: proje klasoru tasindiginda venv/bin/activate eski yolu gosterir."
    info "Sanal ortam yeniden olusturuluyor..."
    remove_venv
fi

if [ ! -d "$VENV_DIR" ]; then
    info "Sanal ortam olusturuluyor: $VENV_DIR"
    if ! "$PYTHON3" -m venv "$VENV_DIR"; then
        err "Sanal ortam olusturulamadi."
        err "Cozumler:"
        err "  - brew install python"
        err "  - xcode-select --install   (eksikse)"
        pause_if_tty
        exit 1
    fi
    ok "Sanal ortam olusturuldu"
fi

venv_is_healthy || die "Sanal ortam saglikli degil: $VENV_PY"

ok "Sanal ortam hazir: $("$VENV_PY" --version 2>&1)"

# Sanal ortam icinde pip yoksa ensurepip ile kur.
if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    warn "Sanal ortam icinde pip bulunamadi; ensurepip calistiriliyor..."
    "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 \
        || die "pip kurulamadi. '$VENV_DIR' klasorunu silip tekrar deneyin."
fi

# ------------------------------------------------------------
#  5) Bagimliliklari kontrol et / kur
#      'pip show' yerine gercek import denemesi kullanilir.
# ------------------------------------------------------------
deps_installed() {
    "$VENV_PY" -c 'import requests, bs4, lxml, urllib3' >/dev/null 2>&1
}

info "Bagimliliklar kontrol ediliyor..."
if deps_installed; then
    ok "Bagimliliklar hazir"
else
    info "Bagimliliklar kuruluyor (bu birkac dakika surebilir)..."
    if ! "$VENV_PY" -m pip install --upgrade pip; then
        warn "pip guncellemesi basarisiz oldu, kuruluma devam ediliyor..."
    fi
    if ! "$VENV_PY" -m pip install -r "$REQUIREMENTS"; then
        err "Bagimliliklar kurulamadi. Internet baglantinizi kontrol edin."
        pause_if_tty
        exit 1
    fi
    deps_installed || die "Bagimliliklar kuruldu ama yuklenemiyor. '$VENV_DIR' klasorunu silip tekrar deneyin."
    ok "Bagimliliklar kuruldu"
fi

if [ "$INSTALL_ONLY" -eq 1 ]; then
    ok "Kurulum tamamlandi."
    pause_if_tty
    exit 0
fi

# ------------------------------------------------------------
#  6) Uygulamayi calistir
# ------------------------------------------------------------
printf '\n'
info "Otomasyon baslatiliyor..."
printf '\n'

"$VENV_PY" "$MAIN_SCRIPT"
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
    printf '\n'
    err "Program hata koduyla sonlandi: $EXIT_CODE"
    pause_if_tty
fi

exit "$EXIT_CODE"
