@echo off
REM VibeDeck Launcher — Windows
REM Usage: .\vibe-deck serve --expose --demo
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python -m vibe_deck.cli %*
