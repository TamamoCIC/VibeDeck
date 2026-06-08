@echo off
REM VibeDeck Launcher — Windows

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

REM If no arguments (double-click), show help and keep window open
if "%~1"=="" (
    echo.
    echo   ==== VibeDeck ====
    echo.
    echo   Commands:
    echo     .\vibe-deck serve --expose       Start daemon (LAN)
    echo     .\vibe-deck serve --demo         Sample widgets
    echo     .\vibe-deck status               Show terminals
    echo.
    python -m vibe_deck.cli --help
    echo.
    pause
    exit /b
)

REM Run with arguments
python -m vibe_deck.cli %*
