"""
Tests for VibeDeck CLI.
"""

import sys
import pytest
from vibe_deck.cli import main


def test_cli_version(monkeypatch, capsys):
    """Test --version flag."""
    testargs = ["vibe-deck", "--version"]
    monkeypatch.setattr(sys, "argv", testargs)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0


def test_cli_help(monkeypatch, capsys):
    """Test that no args prints help."""
    testargs = ["vibe-deck"]
    monkeypatch.setattr(sys, "argv", testargs)
    main()
    captured = capsys.readouterr()
    assert "VibeDeck" in captured.out


def test_cli_serve_help(monkeypatch, capsys):
    """Test serve subcommand --help."""
    testargs = ["vibe-deck", "serve", "--help"]
    monkeypatch.setattr(sys, "argv", testargs)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
