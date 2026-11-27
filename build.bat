@echo off
REM Build standalone Windows executable -> dist\ResiTrack.exe
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -r requirements.txt
pyinstaller --noconfirm --clean ResiTrack.spec
echo.
echo Selesai: dist\ResiTrack.exe
pause
