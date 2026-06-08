@echo off
REM ================================================================
REM  VibeDeck Launcher — Windows
REM  Double-click: start daemon + open browser
REM  Terminal:     .\vibe-deck serve --expose
REM ================================================================

setlocal enabledelayedexpansion
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

REM ── If arguments provided, run directly ──
if not "%~1"=="" (
    python -m vibe_deck.cli %*
    goto :eof
)

REM ── Double-click: auto-start daemon + open browser ──
title VibeDeck

REM Check if daemon is already running
python -c "import urllib.request; urllib.request.urlopen('http://localhost:9734/api/frame', timeout=2)" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   VibeDeck daemon is already running.
    echo   Opening web UI...
    start http://localhost:9734
    goto :eof
)

REM Start daemon
echo.
echo   ==== VibeDeck ====
echo.
echo   Starting daemon...
echo.

REM Open browser after a short delay (daemon needs time to start)
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:9734"

REM Run daemon in foreground (Ctrl+C to stop)
python -m vibe_deck.cli serve --expose

echo.
echo   VibeDeck stopped.
pause
