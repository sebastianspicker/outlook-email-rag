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
    parser.add_argument("--to", default=None, help="Optional To recipient filter (partial match).")
    parser.add_argument("--bcc", default=None, help="Optional BCC recipient filter (partial match).")
    parser.add_argument("--has-attachments", action="store_true", default=None, help="Filter to emails with attachments.")
    parser.add_argument("--priority", type=int, default=None, help="Minimum priority level (integer).")
    parser.add_argument(
        "--email-type",
        choices=["reply", "forward", "original"],
        default=None,
        help="Filter by email type (reply, forward, or original).",
    )
    parser.add_argument("--date-from", type=_parse_iso_date, default=None, help="Start date (YYYY-MM-DD).")
    parser.add_argument("--date-to", type=_parse_iso_date, default=None, help="End date (YYYY-MM-DD).")
    parser.add_argument(
        "--min-score",
        type=_score_float,
        default=None,
        help="Optional minimum relevance score threshold (0.0-1.0).",
    )
    parser.add_argument("--rerank", action="store_true", help="Re-rank results with cross-encoder for better precision.")
    parser.add_argument("--hybrid", action="store_true", help="Use hybrid semantic + BM25 keyword search.")
    parser.add_argument("--topic", type=int, default=None, metavar="TOPIC_ID", help="Filter by topic ID.")
    parser.add_argument("--cluster-id", type=int, default=None, metavar="CLUSTER_ID", help="Filter by cluster ID.")
    parser.add_argument("--expand-query", action="store_true", help="Expand query with related terms.")
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

    # Analytics commands (require SQLite)
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="Show query suggestions based on indexed data and exit.",
    )

    parser.add_argument(
        "--top-contacts",
        metavar="EMAIL",
        default=None,
        help="Show top contacts for an email address and exit.",
    )
    parser.add_argument(
        "--volume",
        choices=["day", "week", "month"],
        default=None,
        help="Show email volume over time by period and exit.",
    )
    parser.add_argument(
        "--entities",
        nargs="?",
        const="all",
        default=None,
        metavar="TYPE",
        help="Show top entities (optionally by type: organization/url/phone/mention/email) and exit.",
    )
    parser.add_argument(
        "--heatmap",
        action="store_true",
        help="Show activity heatmap (hour × day-of-week) and exit.",
    )
    parser.add_argument(
        "--response-times",
        action="store_true",
        help="Show average response times per replier and exit.",
    )
    parser.add_argument(
        "--generate-report",
        nargs="?",
        const="report.html",
        default=None,
        metavar="OUTPUT",
        help="Generate an HTML archive report (default: report.html) and exit.",
    )
    parser.add_argument(
        "--export-network",
        nargs="?",
        const="network.graphml",
        default=None,
        metavar="OUTPUT",
        help="Export communication network as GraphML (default: network.graphml) and exit.",
    )

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
    to: str | None = None,
    bcc: str | None = None,
    has_attachments: bool | None = None,
    priority: int | None = None,
    email_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_score: float | None = None,
    rerank: bool = False,
    hybrid: bool = False,
    topic_id: int | None = None,
    cluster_id: int | None = None,
    expand_query: bool = False,
) -> int:
    """Run a single query and print output. Returns process exit code."""
    results = retriever.search_filtered(
        query=query,
        top_k=top_k,
        sender=sender,
        subject=subject,
        folder=folder,
        cc=cc,
        to=to,
        bcc=bcc,
        has_attachments=has_attachments,
        priority=priority,
        email_type=email_type,
        date_from=date_from,
        date_to=date_to,
        min_score=min_score,
        rerank=rerank,
        hybrid=hybrid,
        topic_id=topic_id,
        cluster_id=cluster_id,
        expand_query=expand_query,
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

    # Suggest command
    if args.suggest:
        _run_suggest()
        sys.exit(0)

    # Report / export commands
    if args.generate_report is not None:
        _run_generate_report(args.generate_report)
        sys.exit(0)

    if args.export_network is not None:
        _run_export_network(args.export_network)
        sys.exit(0)

    # Analytics commands (require SQLite)
    if any([args.top_contacts, args.volume, args.entities is not None, args.heatmap, args.response_times]):
        _run_analytics_command(args)
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
            to=args.to,
            bcc=args.bcc,
            has_attachments=True if args.has_attachments else None,
            priority=args.priority,
            email_type=args.email_type,
            date_from=args.date_from,
            date_to=args.date_to,
            min_score=args.min_score,
            rerank=args.rerank,
            hybrid=args.hybrid,
            topic_id=args.topic,
            cluster_id=args.cluster_id,
            expand_query=args.expand_query,
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


def _get_email_db():
    """Get EmailDatabase instance from settings, or exit with error."""
    import os

    settings = get_settings()
    sqlite_path = settings.sqlite_path
    if not sqlite_path or not os.path.exists(sqlite_path):
        print("SQLite database not found. Run ingestion first:")
        print("  python -m src.ingest data/your-export.olm --extract-entities")
        sys.exit(1)

    from .email_db import EmailDatabase

    return EmailDatabase(sqlite_path)


def _run_analytics_command(args: argparse.Namespace) -> None:
    """Dispatch analytics commands."""
    db = _get_email_db()

    if args.top_contacts:
        _run_top_contacts(db, args.top_contacts)
    elif args.volume:
        _run_volume(db, args.volume)
    elif args.entities is not None:
        entity_type = args.entities if args.entities != "all" else None
        _run_entities(db, entity_type)
    elif args.heatmap:
        _run_heatmap(db)
    elif args.response_times:
        _run_response_times(db)


def _run_top_contacts(db, email_address: str) -> None:
    contacts = db.top_contacts(email_address, limit=20)
    if not contacts:
        print(f"No contacts found for {email_address}")
        return
    print(f"\nTop contacts for {email_address}:\n")
    for contact in contacts:
        print(f"  {contact['total_count']:>4}x  {contact['partner']}")


def _run_volume(db, period: str) -> None:
    from .temporal_analysis import TemporalAnalyzer

    analyzer = TemporalAnalyzer(db)
    data = analyzer.volume_over_time(period=period)
    if not data:
        print("No volume data available.")
        return
    print(f"\nEmail volume by {period}:\n")
    for row in data:
        bar = "█" * min(50, row["count"])
        print(f"  {row['period']}  {row['count']:>5}  {bar}")


def _run_entities(db, entity_type: str | None) -> None:
    entities = db.top_entities(entity_type=entity_type, limit=30)
    if not entities:
        label = entity_type or "all types"
        print(f"No entities found ({label}).")
        return
    label = entity_type or "all"
    print(f"\nTop entities ({label}):\n")
    for ent in entities:
        print(f"  {ent['mention_count']:>4}x  [{ent['entity_type']}]  {ent['entity_text']}")


def _run_heatmap(db) -> None:
    from .temporal_analysis import TemporalAnalyzer

    analyzer = TemporalAnalyzer(db)
    data = analyzer.activity_heatmap()
    if not data:
        print("No heatmap data available.")
        return

    # Build grid: rows=hours (0-23), cols=days (0=Mon-6=Sun)
    grid: dict[tuple[int, int], int] = {}
    max_count = 0
    for row in data:
        key = (row["hour"], row["day_of_week"])
        grid[key] = row["count"]
        max_count = max(max_count, row["count"])

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print("\nActivity heatmap (hour × day-of-week):\n")
    print(f"      {'   '.join(days)}")
    levels = " ░▒▓█"
    for hour in range(24):
        row_str = f"  {hour:02d}  "
        for day in range(7):
            count = grid.get((hour, day), 0)
            if max_count > 0:
                level = int((count / max_count) * (len(levels) - 1))
            else:
                level = 0
            row_str += f" {levels[level]}  "
        print(row_str)
    print(f"\n  Legend: ' '=0  ░=low  ▒=mid  ▓=high  █=peak (max={max_count})")


def _run_response_times(db) -> None:
    from .temporal_analysis import TemporalAnalyzer

    analyzer = TemporalAnalyzer(db)
    data = analyzer.response_times(limit=20)
    if not data:
        print("No response time data available.")
        return
    print("\nAverage response times:\n")
    for row in data:
        print(
            f"  {row['avg_response_hours']:>6.1f}h avg  "
            f"({row['response_count']:>3} replies)  {row['replier']}"
        )


def _run_suggest() -> None:
    db = _get_email_db()
    from .query_suggestions import QuerySuggester

    suggester = QuerySuggester(db)
    suggestions = suggester.suggest_flat(limit=15)
    if not suggestions:
        print("No suggestions available. Is the SQLite database populated?")
        return
    print("\nQuery suggestions:\n")
    for suggestion in suggestions:
        print(f"  • {suggestion}")


def _run_generate_report(output_path: str) -> None:
    db = _get_email_db()
    from .report_generator import ReportGenerator

    generator = ReportGenerator(db)
    generator.generate(output_path=output_path)
    print(f"Report generated: {output_path}")


def _run_export_network(output_path: str) -> None:
    db = _get_email_db()
    from .network_analysis import CommunicationNetwork

    net = CommunicationNetwork(db)
    result = net.export_graphml(output_path)
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print(f"Network exported: {output_path}")
    print(f"  Nodes: {result['total_nodes']}, Edges: {result['total_edges']}")


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
        or args.to
        or args.bcc
        or args.has_attachments
        or args.priority is not None
        or args.email_type is not None
        or args.date_from
        or args.date_to
        or args.min_score is not None
        or args.json
        or args.format is not None
        or args.topic is not None
        or args.cluster_id is not None
        or args.expand_query
    ):
        parser.error(
            "--sender/--subject/--folder/--cc/--to/--bcc/--has-attachments/"
            "--priority/--email-type/--date-from/--date-to/--min-score/"
            "--topic/--cluster-id/--expand-query/--json/--format require --query"
        )

    analytics_modes = [
        bool(args.top_contacts), bool(args.volume), args.entities is not None,
        bool(args.heatmap), bool(args.response_times),
    ]
    operational_modes = [
        bool(args.stats), bool(args.list_senders), bool(args.reset_index),
        bool(args.suggest),
        args.generate_report is not None,
        args.export_network is not None,
        *analytics_modes,
    ]
    if sum(operational_modes) > 1:
        parser.error(
            "--stats, --list-senders, --reset-index, --suggest, --generate-report, "
            "--export-network, --top-contacts, --volume, "
            "--entities, --heatmap, and --response-times are mutually exclusive"
        )

    if args.query and any(operational_modes):
        parser.error(
            "--query cannot be combined with operational commands "
            "(--stats, --list-senders, --reset-index, --top-contacts, etc.)"
        )


def resolve_output_format(args: argparse.Namespace) -> OutputFormat:
    if args.format is not None:
        return args.format
    if args.json:
        return "json"
    return "text"


if __name__ == "__main__":
    main()
