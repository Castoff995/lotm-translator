@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing_python
".venv\Scripts\python.exe" -m src.lotm_translator.cli corpus-studio
goto done

:missing_python
echo Python environment .venv was not found in the project folder.

:done
pause
