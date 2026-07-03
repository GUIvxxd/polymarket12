from __future__ import annotations

from polybot.main import build_parser, main


def test_help_contains_paper_trading_description() -> None:
    parser = build_parser()

    help_text = parser.format_help()

    assert "paper-trading" in help_text
    assert "status" in help_text


def test_status_command_reports_paper_mode(capsys) -> None:
    exit_code = main(["status"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "mode: paper" in captured.out
    assert "paper_trades.sqlite" in captured.out

