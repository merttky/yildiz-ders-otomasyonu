@echo off
REM ============================================================
REM  YILDIZ Ders Otomasyonu - Windows Baslatici
REM  v2.0 (self-healing: venv yolu bagimliligi yok)
REM ============================================================
REM
REM Not: bu betik sanal ortami (venv) "call activate" ile AKTIF ETMEZ.
REM Instead dogrudan "%VENV_DIR%\Scripts\python.exe" cagirir. Boylece:
REM   - Proje klasoru tasindiginda activate.bat icindeki eski VIRTUAL_ENV
REM     yolu calismaya devam eder.
REM   - "pip" komutu tanimli degilse hata vermez; her zaman
REM     "python -m pip" kullanilir.
REM ============================================================

setlocal EnableExtensions

set "VENV_DIR=venv"
set "REQUIREMENTS=requirements.txt"
set "MAIN_SCRIPT=main.py"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "FORCE_RECREATE=0"
set "INSTALL_ONLY=0"

REM ------------------------------------------------------------
REM  Argumanlari isle
REM  Not: her kosul kendi blogu icinde tamamlanir; "goto" etiketleri
REM  hicbir zaman "()" blogu icinde degil.
REM ------------------------------------------------------------
:parse_args
if "%~1"=="" goto after_args
if /i "%~1"=="--recreate-venv" (
    set "FORCE_RECREATE=1"
    shift
    goto parse_args
)
if /i "%~1"=="--install-only" (
    set "INSTALL_ONLY=1"
    shift
    goto parse_args
)
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage
echo Bilinmeyen secenek: %~1
goto usage

:after_args
echo ========================================
echo  YILDIZ Ders Otomasyonu v2.0
echo ========================================
echo.

REM ------------------------------------------------------------
REM  1) Proje klasorune gec
REM ------------------------------------------------------------
cd /d "%~dp0"
if errorlevel 1 (
    echo [HATA] Proje klasorune gecilemedi.
    pause
    exit /b 1
)
echo [INFO] Proje klasoru: %CD%

REM ------------------------------------------------------------
REM  2) Python 3 bul (once "py -3", sonra "python")
REM ------------------------------------------------------------
set "PYTHON_CMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"

if defined PYTHON_CMD goto python_found

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"

if defined PYTHON_CMD goto python_found

echo [HATA] Python 3.8+ bulunamadi.
echo.
echo Lutfen kurun:
echo   winget install Python.Python.3.12
echo veya https://www.python.org/downloads/
echo.
echo Kurulumda "Add python.exe to PATH" secenegini isaretlemeyi unutmayin.
echo.
pause
exit /b 1

:python_found
for /f "delims=" %%v in ('%PYTHON_CMD% --version 2^>^&1') do echo [OK] Python bulundu: %%v

REM tkinter (GUI icin gerekli) kontrolu
%PYTHON_CMD% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo [UYARI] tkinter bulunamadi; arayuz acilamayabilir.
    echo [UYARI] Python kurulumunu "tcl/tk and IDLE" secenegiyle tekrar calistirin.
    echo.
)

if not exist "%REQUIREMENTS%" (
    echo [HATA] requirements.txt bulunamadi.
    pause
    exit /b 1
)
if not exist "%MAIN_SCRIPT%" (
    echo [HATA] main.py bulunamadi.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM  3) Sanal ortami saglikli mi? Degilse yeniden olustur.
REM ------------------------------------------------------------
if "%FORCE_RECREATE%"=="1" goto maybe_recreate
goto check_venv

:maybe_recreate
if not exist "%VENV_DIR%" goto check_venv
echo [INFO] '--recreate-venv' verildi; eski sanal ortam siliniyor...
goto remove_venv

:check_venv
if not exist "%VENV_DIR%" goto create_venv
if not exist "%VENV_PY%" goto venv_broken
"%VENV_PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto venv_broken
goto venv_ok

:venv_broken
echo [UYARI] '%VENV_DIR%' bozuk veya eski konumdan kalmis.
echo [UYARI] Nedeni: proje klasoru tasindiginda activate.bat eski yolu gosterir.
echo [INFO] Sanal ortam yeniden olusturuluyor...
goto remove_venv

:remove_venv
if exist "%VENV_DIR%\pyvenv.cfg" goto do_remove
if exist "%VENV_DIR%\Scripts\activate.bat" goto do_remove
echo [HATA] '%VENV_DIR%' bir sanal ortam gibi gorunmuyor; guvenlik icin silinmedi.
echo [HATA] Lutfen klasoru elle kontrol edin veya silin.
pause
exit /b 1

:do_remove
rmdir /s /q "%VENV_DIR%"

REM ------------------------------------------------------------
REM  4) Sanal ortami olustur
REM ------------------------------------------------------------
:create_venv
echo [INFO] Sanal ortam olusturuluyor: %VENV_DIR%
%PYTHON_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [HATA] Sanal ortam olusturulamadi.
    echo [HATA] Cozumler:
    echo   - Python'u "pip" ve "venv" bilesenleriyle birlikte kurun
    echo   - Proje klasorunu Program Files gibi yazma korumali yere tasimayin
    echo.
    pause
    exit /b 1
)
echo [OK] Sanal ortam olusturuldu

:venv_ok
if not exist "%VENV_PY%" goto venv_unhealthy
"%VENV_PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto venv_unhealthy
for /f "delims=" %%v in ('"%VENV_PY%" --version 2^>^&1') do echo [OK] Sanal ortam hazir: %%v
goto pip_check

:venv_unhealthy
echo [HATA] Sanal ortam saglikli degil: %VENV_PY%
pause
exit /b 1

REM Sanal ortam icinde pip yoksa ensurepip ile kur.
:pip_check
"%VENV_PY%" -m pip --version >nul 2>&1
if not errorlevel 1 goto deps_check
echo [UYARI] Sanal ortam icinde pip bulunamadi; ensurepip calistiriliyor...
"%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
if not errorlevel 1 goto deps_check
echo [HATA] pip kurulamadi. '%VENV_DIR%' klasorunu silip tekrar deneyin.
pause
exit /b 1

REM ------------------------------------------------------------
REM  5) Bagimliliklari kontrol et / kur
REM      "pip show" yerine gercek import denemesi kullanilir.
REM ------------------------------------------------------------
:deps_check
echo [INFO] Bagimliliklar kontrol ediliyor...
"%VENV_PY%" -c "import requests, bs4, lxml, urllib3" >nul 2>&1
if not errorlevel 1 (
    echo [OK] Bagimliliklar hazir
    goto deps_done
)

echo [INFO] Bagimliliklar kuruluyor (bu birkac dakika surebilir)...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 echo [UYARI] pip guncellemesi basarisiz oldu, kuruluma devam ediliyor...

"%VENV_PY%" -m pip install -r "%REQUIREMENTS%"
if errorlevel 1 (
    echo [HATA] Bagimliliklar kurulamadi. Internet baglantinizi kontrol edin.
    pause
    exit /b 1
)

"%VENV_PY%" -c "import requests, bs4, lxml, urllib3" >nul 2>&1
if errorlevel 1 (
    echo [HATA] Bagimliliklar kuruldu ama yuklenemiyor.
    echo [HATA] '%VENV_DIR%' klasorunu silip tekrar deneyin.
    pause
    exit /b 1
)
echo [OK] Bagimliliklar kuruldu

:deps_done
if "%INSTALL_ONLY%"=="1" (
    echo [OK] Kurulum tamamlandi.
    pause
    exit /b 0
)

REM ------------------------------------------------------------
REM  6) Uygulamayi calistir
REM ------------------------------------------------------------
echo.
echo [INFO] Otomasyon baslatiliyor...
echo.

"%VENV_PY%" "%MAIN_SCRIPT%"
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [HATA] Program hata koduyla sonlandi: %EXIT_CODE%
    pause
)

exit /b %EXIT_CODE%


REM ============================================================
REM  Yardim metni
REM ============================================================
:usage
echo Kullanim: run_windows.bat [secenekler]
echo.
echo Secenekler:
echo   --recreate-venv   Mevcut sanal ortami silip yeniden olusturur
echo   --install-only    Sadece bagimliliklari kurar, uygulamayi calistirmaz
echo   -h, --help        Bu yardim metnini gosterir
exit /b 0
