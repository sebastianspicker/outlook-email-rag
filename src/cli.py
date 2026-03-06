"""Interactive and single-shot CLI for searching indexed emails."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import TYPE_CHECKING, Any, Literal

from dotenv import load_dotenv

from .config import configure_logging, get_settings
from .sanitization import sanitize_untrusted_text
from .validation import parse_iso_date, positive_int, validate_date_window

if TYPE_CHECKING:
    from .retriever import EmailRetriever

logger = logging.getLogger(__name__)
OutputFormat = Literal["text", "json"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for both operational and query commands."""
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Search your email archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m src.cli --query \"invoice from vendor\" --sender billing@vendor.com\n"
            "  python -m src.cli --query \"security review\" --format json\n"
            "  python -m src.cli --stats\n"
        ),
    )
    parser.add_argument("--version", action="version", version="0.1.0")
    parser.add_argument("--query", "-q", help="Single query (omit for interactive mode).")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (legacy alias for --format json).",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default=None,
        help="Output format for --query results (text or json).",
    )
    parser.add_argument(
        "--top-k",
        type=_top_k_int,
        default=settings.top_k,
        help="Number of results to retrieve.",
    )
    parser.add_argument("--sender", default=None, help="Optional sender filter (partial name/email match).")
    parser.add_argument("--subject", default=None, help="Optional subject filter (partial match).")
    parser.add_argument("--folder", default=None, help="Optional folder filter (partial match).")
    parser.add_argument("--cc", default=None, help="Optional CC recipient filter (partial match).")
    parser.add_argument("--date-from", type=_parse_iso_date, default=None, help="Start date (YYYY-MM-DD).")
    parser.add_argument("--date-to", type=_parse_iso_date, default=None, help="End date (YYYY-MM-DD).")
    parser.add_argument(
        "--min-score",
        type=_score_float,
        default=None,
        help="Optional minimum relevance score threshold (0.0-1.0).",
    )
    parser.add_argument("--chromadb-path", default=None, help="Custom ChromaDB path.")

    parser.add_argument("--stats", action="store_true", help="Print archive statistics and exit.")
    parser.add_argument(
        "--list-senders",
        type=_positive_int_arg,
        default=0,
        metavar="N",
        help="List top N senders and exit.",
    )
    parser.add_argument(
        "--reset-index",
        action="store_true",
        help="Delete and recreate the email collection.",
    )
    parser.add_argument("--yes", action="store_true", help="Confirm destructive operations.")
    parser.add_argument("--log-level", default=None, help="Logging level override.")

    args = parser.parse_args(argv)
    _validate_arg_combinations(args, parser)
    return args


def run_interactive(retriever: "EmailRetriever", top_k: int = 10) -> None:
    """Run interactive search loop."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
    except ImportError:
        print("Interactive mode requires 'rich'. Install dependencies from requirements.txt")
        return

    console = Console()
    _render_interactive_intro(console, Panel, retriever)

    while True:
        try:
            query = console.input("\n[bold cyan]Search:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        action = _interactive_action(query)
        if action == "empty":
            continue
        if action == "quit":
            break
        if action == "stats":
            _render_stats(console, retriever)
            continue
        if action == "senders":
            _render_senders(console, retriever)
            continue

        results = retriever.search_filtered(query=query, top_k=top_k)
        if not results:
            console.print("[yellow]No matching emails found.[/]")
            console.print("[dim]Try refining query terms, sender filter, or date window.[/]")
            continue

        _render_results_table(console, Table, results)


def run_single_query(
    retriever: "EmailRetriever",
    query: str,
    as_json: bool = False,
    top_k: int = 10,
    sender: str | None = None,
    subject: str | None = None,
    folder: str | None = None,
    cc: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_score: float | None = None,
) -> int:
    """Run a single query and print output. Returns process exit code."""
    results = retriever.search_filtered(
        query=query,
        top_k=top_k,
        sender=sender,
        subject=subject,
        folder=folder,
        cc=cc,
        date_from=date_from,
        date_to=date_to,
        min_score=min_score,
    )

    if as_json:
        print(json.dumps(retriever.serialize_results(query, results), indent=2))
        return 0

    if not results:
        print("No matching emails found.")
        print("Try refining query terms, sender filter, or date window.")
        return 0

    for index, result in enumerate(results, 1):
        print(f"\n{'=' * 60}")
        print(f"Result {index} (relevance: {result.score:.2f})")
        print(sanitize_untrusted_text(result.to_context_string()))
    return 0


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    configure_logging(args.log_level)

    try:
        from .retriever import EmailRetriever
    except ModuleNotFoundError as exc:
        print("Missing runtime dependency. Install project dependencies first:")
        print("  pip install -r requirements.txt")
        print(f"Details: {exc}")
        sys.exit(2)

    retriever = EmailRetriever(chromadb_path=args.chromadb_path)

    if args.reset_index:
        if not args.yes:
            print("Refusing to reset index without --yes.")
            sys.exit(2)
        retriever.reset_index()
        print("Index has been reset.")
        sys.exit(0)

    if args.stats:
        print(json.dumps(retriever.stats(), indent=2))
        sys.exit(0)

    if args.list_senders:
        _print_sender_lines(retriever.list_senders(args.list_senders), print_fn=print)
        sys.exit(0)

    if retriever.collection.count() == 0:
        print("No emails in database. Run ingestion first:")
        print("  python -m src.ingest data/your-export.olm")
        print("Or use the email_ingest MCP tool from Claude Code.")
        sys.exit(1)

    if args.query:
        output_format = resolve_output_format(args)
        code = run_single_query(
            retriever,
            query=args.query,
            as_json=(output_format == "json"),
            top_k=args.top_k,
            sender=args.sender,
            subject=args.subject,
            folder=args.folder,
            cc=args.cc,
            date_from=args.date_from,
            date_to=args.date_to,
            min_score=args.min_score,
        )
        sys.exit(code)

    run_interactive(retriever, top_k=args.top_k)


def _print_sender_lines(senders: list[dict[str, Any]], print_fn=print) -> None:
    if not senders:
        print_fn("No senders found.")
        return

    for sender in senders:
        safe_name = sanitize_untrusted_text(str(sender["name"]))
        safe_email = sanitize_untrusted_text(str(sender["email"]))
        print_fn(f"{sender['count']:>4}x  {safe_name} <{safe_email}>")


def _interactive_action(query: str) -> Literal["empty", "quit", "stats", "senders", "search"]:
    normalized = query.strip().lower()
    if not normalized:
        return "empty"
    if normalized in {"quit", "exit", "q"}:
        return "quit"
    if normalized == "stats":
        return "stats"
    if normalized == "senders":
        return "senders"
    return "search"


def _render_interactive_intro(console, panel_cls, retriever: "EmailRetriever") -> None:
    stats = retriever.stats()
    console.print(
        panel_cls(
            f"Emails: {stats.get('total_emails', 0)} | "
            f"Chunks: {stats.get('total_chunks', 0)} | "
            f"Senders: {stats.get('unique_senders', 0)} | "
            f"Range: {stats.get('date_range', {}).get('earliest', '?')} -> {stats.get('date_range', {}).get('latest', '?')}",
            title="Email RAG",
            subtitle="Type 'quit' to exit, 'stats' for details, 'senders' to list senders",
        )
    )


def _render_stats(console, retriever: "EmailRetriever") -> None:
    console.print_json(json.dumps(retriever.stats(), indent=2))


def _render_senders(console, retriever: "EmailRetriever") -> None:
    _print_sender_lines(retriever.list_senders(30), print_fn=console.print)


def _render_results_table(console, table_cls, results) -> None:
    table = table_cls(title=f"Top {len(results)} results")
    table.add_column("#", style="dim")
    table.add_column("Score")
    table.add_column("Subject")
    table.add_column("Sender")
    table.add_column("Date")

    for index, result in enumerate(results[:10], 1):
        metadata = result.metadata
        subject = sanitize_untrusted_text(str(metadata.get("subject", "(no subject)")))
        sender_value = metadata.get("sender_name") or metadata.get("sender_email", "?")
        sender = sanitize_untrusted_text(str(sender_value))
        date_value = sanitize_untrusted_text(str(metadata.get("date", "?"))[:10])
        table.add_row(
            str(index),
            f"{result.score:.0%}",
            subject,
            sender,
            date_value,
        )

    console.print(table)


def _parse_iso_date(value: str) -> str:
    try:
        return parse_iso_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Use YYYY-MM-DD.") from exc


def _positive_int_arg(value: str) -> int:
    try:
        return positive_int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _top_k_int(value: str) -> int:
    parsed = _positive_int_arg(value)
    if parsed > 1000:
        raise argparse.ArgumentTypeError("Value must be <= 1000.")
    return parsed


def _score_float(value: str) -> float:
    parsed = float(value)
    if not (0.0 <= parsed <= 1.0):
        raise argparse.ArgumentTypeError("Value must be between 0.0 and 1.0.")
    return parsed


def _validate_arg_combinations(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    try:
        validate_date_window(args.date_from, args.date_to)
    except ValueError:
        parser.error("--date-from cannot be later than --date-to")

    if args.json and args.format is not None:
        parser.error("--json cannot be combined with --format; use only --format {text,json}")

    if args.query is None and (
        args.sender
        or args.subject
        or args.folder
        or args.cc
        or args.date_from
        or args.date_to
        or args.min_score is not None
        or args.json
        or args.format is not None
    ):
        parser.error("--sender/--subject/--folder/--cc/--date-from/--date-to/--min-score/--json/--format require --query")

    operational_modes = [bool(args.stats), bool(args.list_senders), bool(args.reset_index)]
    if sum(operational_modes) > 1:
        parser.error("--stats, --list-senders, and --reset-index are mutually exclusive")

    if args.query and any(operational_modes):
        parser.error("--query cannot be combined with --stats, --list-senders, or --reset-index")


def resolve_output_format(args: argparse.Namespace) -> OutputFormat:
    if args.format is not None:
        return args.format
    if args.json:
        return "json"
    return "text"


if __name__ == "__main__":
    main()
