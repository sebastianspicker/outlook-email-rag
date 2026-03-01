import pytest


def test_cli_rejects_non_positive_top_k():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--query", "budget", "--top-k", "0"])


def test_cli_rejects_negative_list_senders():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--list-senders", "-1"])


def test_cli_rejects_zero_list_senders():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--list-senders", "0"])


def test_cli_rejects_too_large_top_k():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--query", "budget", "--top-k", "1001"])


def test_cli_rejects_multiple_operational_modes():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--stats", "--list-senders", "10"])


def test_cli_rejects_query_with_operational_mode():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--query", "budget", "--stats"])
