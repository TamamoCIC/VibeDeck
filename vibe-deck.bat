@echo off
REM VibeDeck Launcher — Windows
REM Runs vibe-deck with proper UTF-8 encoding
set PYTHONUTF8=1
python -m vibe_deck.cli %*
