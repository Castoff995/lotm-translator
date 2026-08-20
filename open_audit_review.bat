@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Не найдена рабочая Python-среда .venv\Scripts\python.exe
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m src.lotm_translator.cli review-audit
if errorlevel 1 pause
