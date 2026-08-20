@echo off
cd /d "%~dp0"
setlocal
set /p CHAPTER=Chapter number (4 digits, e.g. 0001): 
if "%CHAPTER%"=="" exit /b

set "BASE=data\raw\official_ru_apple\chapters\ch_%CHAPTER%_ru_official_apple"
set "IMAGES=lotm\ocr_input\chapter%CHAPTER%"

if not exist "%BASE%_qwen_review.json" (
  echo Qwen review was not found for chapter %CHAPTER%.
  echo Build and review Apple OCR for this chapter first.
  pause
  exit /b 1
)

.venv\Scripts\python.exe -m src.lotm_translator.cli review-ocr-ui ^
  "%BASE%_qwen_review.json" ^
  "%BASE%_review.json" ^
  --images "%IMAGES%"
