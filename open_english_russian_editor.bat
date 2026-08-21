@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set /p CHAPTER=Номер главы (4 цифры, например 0001): 
if "%CHAPTER%"=="" goto :eof

set "PYTHON=.venv\Scripts\python.exe"
set "ALIGNMENT=data\processed\english_vs_russian_pair\ch_%CHAPTER%_alignment.json"
set "ENGLISH=data\processed\english_paragraph_units\ch_%CHAPTER%_en.txt"
set "RUSSIAN=data\processed\english_vs_russian_pair\ch_%CHAPTER%_russian_pair_units.txt"

if not exist "%PYTHON%" (
  echo Не найден .venv\Scripts\python.exe. Запусти батник из папки проекта.
  pause
  exit /b 1
)
if not exist "%ALIGNMENT%" (
  echo Нет готовой English - Russian связки для главы %CHAPTER%.
  echo Сначала нужно подготовить английские абзацы и выполнить выравнивание.
  pause
  exit /b 1
)
if not exist "%ENGLISH%" (
  echo Не найден файл английских абзацев: %ENGLISH%
  pause
  exit /b 1
)
if not exist "%RUSSIAN%" (
  echo Не найден файл русских групп: %RUSSIAN%
  pause
  exit /b 1
)

"%PYTHON%" -m src.lotm_translator.cli edit-english-russian-pair "%ALIGNMENT%" "%ENGLISH%" "%RUSSIAN%"
pause
