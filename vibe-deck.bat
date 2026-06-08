@echo off
REM ================================================================
REM  VibeDeck Launcher — Windows
REM  Double-click : opens persistent terminal
REM  Terminal     : .\vibe-deck serve --expose
REM ================================================================

setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

REM ── Already in a terminal with args: run directly ──
if not "%~1"=="" (
    python -m vibe_deck.cli %*
    goto :eof
)

REM ── Double-click: open persistent terminal window ──
start "VibeDeck" cmd /k "cd /d "%~dp0" && chcp 65001 >nul 2>&1 && set PYTHONUTF8=1 && set PYTHONIOENCODING=utf-8 && title VibeDeck && echo. && echo   ==== VibeDeck ==== && echo. && echo   python -m vibe_deck.cli serve --expose    (daemon + LAN) && echo   python -m vibe_deck.cli serve --demo      (sample widgets) && echo   python -m vibe_deck.cli status            (terminals + agents) && echo   python -m vibe_deck.cli --help            (all commands) && echo. && echo   Type a command or 'exit' to close. && echo."
