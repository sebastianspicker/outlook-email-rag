import pytest


def test_parse_args_supports_filter_flags():
    from src.cli import parse_args

    args = parse_args(
        [
            "--query",
            "budget",
            "--sender",
            "john",
            "--date-from",
            "2023-01-01",
            "--date-to",
            "2023-12-31",
            "--json",
            "--no-claude",
            "--top-k",
            "5",
        ]
    )

    assert args.query == "budget"
    assert args.sender == "john"
    assert args.date_from == "2023-01-01"
    assert args.date_to == "2023-12-31"
    assert args.json is True
    assert args.no_claude is True
    assert args.top_k == 5


def test_parse_args_rejects_invalid_dates():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--query", "test", "--date-from", "2023/01/01"])
