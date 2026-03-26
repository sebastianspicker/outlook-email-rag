"""Interactive and single-shot CLI for searching indexed emails.

Supports both modern subcommands and legacy flat-flag syntax:

  Modern:
    python -m src.cli search "invoice from vendor" --sender billing@vendor.com
    python -m src.cli analytics stats
    python -m src.cli export thread CONV_ID --format pdf

  Legacy (deprecated, still works):
    python -m src.cli --query "invoice from vendor" --sender billing@vendor.com
    python -m src.cli --stats
"""

from __future__ import annotations

import argparse
import sys
import warnings
from typing import Any

from dotenv import load_dotenv

# Re-export command handlers so existing imports keep working
# (e.g. ``from src.cli import _cmd_search, run_single_query``).
from .cli_commands import (  # noqa: F401
    _cmd_admin,
    _cmd_analytics,
    _cmd_browse,
    _cmd_evidence,
    _cmd_export,
    _cmd_legacy,
    _cmd_search,
    _cmd_training,
    _get_email_db,
    _interactive_action,
    _print_sender_lines,
    _render_interactive_intro,
    _render_results_table,
    _render_senders,
    _render_stats,
    _run_analytics_command,
    _run_browse,
    _run_custody_chain,
    _run_dossier,
    _run_entities,
    _run_evidence_export,
    _run_evidence_list,
    _run_evidence_stats,
    _run_evidence_verify,
    _run_export_email,
    _run_export_network,
    _run_export_thread,
    _run_fine_tune,
    _run_generate_report,
    _run_generate_training_data,
    _run_heatmap,
    _run_provenance,
    _run_response_times,
    _run_suggest,
    _run_top_contacts,
    _run_volume,
    resolve_output_format,
    run_interactive,
    run_single_query,
)
from .config import configure_logging, get_settings
from .validation import parse_iso_date, positive_int, score_float, validate_date_window

# ── Type converters (shared by both parsers) ──────────────────────


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
    try:
        return score_float(value)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


# ── Shared flag groups ────────────────────────────────────────────


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by all subcommands."""
    parser.add_argument("--chromadb-path", default=None, help="Custom ChromaDB path.")
    parser.add_argument("--log-level", default=None, help="Logging level override.")


def _add_search_filters(parser: argparse.ArgumentParser) -> None:
    """Add search filter flags to a (sub)parser."""
    settings = get_settings()
    parser.add_argument("--sender", default=None, help="Sender filter (partial name/email match).")
    parser.add_argument("--subject", default=None, help="Subject filter (partial match).")
    parser.add_argument("--folder", default=None, help="Folder filter (partial match).")
    parser.add_argument("--cc", default=None, help="CC recipient filter (partial match).")
    parser.add_argument("--to", default=None, help="To recipient filter (partial match).")
    parser.add_argument("--bcc", default=None, help="BCC recipient filter (partial match).")
    parser.add_argument("--has-attachments", action="store_true", default=None, help="Filter to emails with attachments.")
    parser.add_argument("--priority", type=int, default=None, help="Minimum priority level.")
    parser.add_argument(
        "--email-type",
        choices=["reply", "forward", "original"],
        default=None,
        help="Filter by email type.",
    )
    parser.add_argument("--date-from", type=_parse_iso_date, default=None, help="Start date (YYYY-MM-DD).")
    parser.add_argument("--date-to", type=_parse_iso_date, default=None, help="End date (YYYY-MM-DD).")
    parser.add_argument("--min-score", type=_score_float, default=None, help="Minimum relevance score (0.0-1.0).")
    parser.add_argument("--rerank", action="store_true", help="Re-rank with cross-encoder.")
    parser.add_argument("--hybrid", action="store_true", help="Hybrid semantic + BM25 search.")
    parser.add_argument("--topic", type=int, default=None, metavar="TOPIC_ID", help="Filter by topic ID.")
    parser.add_argument("--cluster-id", type=int, default=None, metavar="CLUSTER_ID", help="Filter by cluster ID.")
    parser.add_argument("--expand-query", action="store_true", help="Expand query with related terms.")
    parser.add_argument(
        "--top-k",
        type=_top_k_int,
        default=settings.top_k,
        help="Number of results to retrieve.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default=None,
        help="Output format (text or json).",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON (alias for --format json).")


# ── Subcommand parser ─────────────────────────────────────────────


def _build_subcommand_parser() -> argparse.ArgumentParser:
    """Build the modern subcommand-based parser."""
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Search your email archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python -m src.cli search "invoice from vendor" --sender billing@vendor.com\n'
            "  python -m src.cli analytics stats\n"
            "  python -m src.cli export thread CONV_ID --format pdf\n"
            "\n"
            "Legacy flat-flag syntax is still supported but deprecated:\n"
            '  python -m src.cli --query "invoice" --sender billing@vendor.com\n'
        ),
    )
    parser.add_argument("--version", action="version", version="0.1.0")
    parser.add_argument("--chromadb-path", default=None, help="Custom ChromaDB path.")
    parser.add_argument("--log-level", default=None, help="Logging level override.")

    subparsers = parser.add_subparsers(dest="subcommand")

    # ── search ────────────────────────────────────────────────────
    search_parser = subparsers.add_parser(
        "search",
        help="Search emails with filters.",
        description="Search emails using natural language queries with optional metadata filters.",
    )
    _add_common_flags(search_parser)
    search_parser.add_argument(
        "query_positional",
        nargs="?",
        default=None,
        metavar="QUERY",
        help="Search query (alternative to --query).",
    )
    search_parser.add_argument("--query", "-q", default=None, help="Search query.")
    _add_search_filters(search_parser)

    # ── browse ────────────────────────────────────────────────────
    browse_parser = subparsers.add_parser(
        "browse",
        help="Browse emails in pages.",
        description="Browse all emails in paginated view for systematic review.",
    )
    _add_common_flags(browse_parser)
    browse_parser.add_argument("--page", type=_positive_int_arg, default=1, help="Page number (default: 1).")
    browse_parser.add_argument("--page-size", type=_positive_int_arg, default=20, help="Emails per page (default: 20, max: 50).")
    browse_parser.add_argument("--folder", default=None, help="Filter by folder.")
    browse_parser.add_argument("--sender", default=None, help="Filter by sender.")

    # ── export ────────────────────────────────────────────────────
    export_parser = subparsers.add_parser(
        "export",
        help="Export emails, threads, and reports.",
        description="Export emails, threads, reports, or network graphs.",
    )
    _add_common_flags(export_parser)
    export_sub = export_parser.add_subparsers(dest="export_action")

    export_thread = export_sub.add_parser("thread", help="Export a conversation thread.")
    export_thread.add_argument("conversation_id", help="Thread conversation ID.")
    export_thread.add_argument("--format", choices=["html", "pdf"], default="html", help="Export format (default: html).")
    export_thread.add_argument("--output", "-o", default=None, help="Output file path.")

    export_email = export_sub.add_parser("email", help="Export a single email.")
    export_email.add_argument("uid", help="Email UID.")
    export_email.add_argument("--format", choices=["html", "pdf"], default="html", help="Export format (default: html).")
    export_email.add_argument("--output", "-o", default=None, help="Output file path.")

    export_report = export_sub.add_parser("report", help="Generate an HTML archive report.")
    export_report.add_argument("--output", "-o", default="report.html", help="Output file path (default: report.html).")

    export_network = export_sub.add_parser("network", help="Export communication network as GraphML.")
    export_network.add_argument("--output", "-o", default="network.graphml", help="Output file path (default: network.graphml).")

    # ── evidence ──────────────────────────────────────────────────
    evidence_parser = subparsers.add_parser(
        "evidence",
        help="Evidence management, custody, and dossier.",
        description="Manage evidence items, chain of custody, and proof dossiers.",
    )
    _add_common_flags(evidence_parser)
    evidence_sub = evidence_parser.add_subparsers(dest="evidence_action")

    ev_list = evidence_sub.add_parser("list", help="List evidence items.")
    ev_list.add_argument("--category", default=None, help="Filter by category.")
    ev_list.add_argument("--min-relevance", type=int, choices=[1, 2, 3, 4, 5], default=None, help="Minimum relevance.")

    ev_export = evidence_sub.add_parser("export", help="Export evidence report.")
    ev_export.add_argument("output_path", help="Output file path.")
    ev_export.add_argument("--format", choices=["html", "csv", "pdf"], default="html", help="Export format.")
    ev_export.add_argument("--category", default=None, help="Filter by category.")
    ev_export.add_argument("--min-relevance", type=int, choices=[1, 2, 3, 4, 5], default=None, help="Minimum relevance.")

    evidence_sub.add_parser("stats", help="Show evidence collection statistics.")
    evidence_sub.add_parser("verify", help="Re-verify all evidence quotes.")

    ev_dossier = evidence_sub.add_parser("dossier", help="Generate proof dossier.")
    ev_dossier.add_argument("output_path", help="Output file path.")
    ev_dossier.add_argument("--format", choices=["html", "pdf"], default="html", help="Dossier format.")
    ev_dossier.add_argument("--category", default=None, help="Filter by category.")
    ev_dossier.add_argument("--min-relevance", type=int, choices=[1, 2, 3, 4, 5], default=None, help="Minimum relevance.")

    evidence_sub.add_parser("custody", help="View chain-of-custody audit trail.")

    ev_prov = evidence_sub.add_parser("provenance", help="View email provenance.")
    ev_prov.add_argument("uid", help="Email UID.")

    # ── analytics ─────────────────────────────────────────────────
    analytics_parser = subparsers.add_parser(
        "analytics",
        help="Statistics, contacts, volume, entities.",
        description="Email archive analytics and statistics.",
    )
    _add_common_flags(analytics_parser)
    analytics_sub = analytics_parser.add_subparsers(dest="analytics_action")

    analytics_sub.add_parser("stats", help="Print archive statistics.")

    an_senders = analytics_sub.add_parser("senders", help="List top senders.")
    an_senders.add_argument("limit", nargs="?", type=_positive_int_arg, default=30, help="Number of senders (default: 30).")

    analytics_sub.add_parser("suggest", help="Show query suggestions.")

    an_contacts = analytics_sub.add_parser("contacts", help="Show top contacts for an email address.")
    an_contacts.add_argument("email_address", help="Email address to look up.")

    an_volume = analytics_sub.add_parser("volume", help="Show email volume over time.")
    an_volume.add_argument(
        "period",
        nargs="?",
        choices=["day", "week", "month"],
        default="month",
        help="Time period (default: month).",
    )

    an_entities = analytics_sub.add_parser("entities", help="Show top entities.")
    an_entities.add_argument(
        "--type",
        dest="entity_type",
        default=None,
        help="Entity type filter (organization/url/phone/mention/email).",
    )

    analytics_sub.add_parser("heatmap", help="Show activity heatmap (hour × day-of-week).")
    analytics_sub.add_parser("response-times", help="Show average response times per replier.")

    # ── training ──────────────────────────────────────────────────
    training_parser = subparsers.add_parser(
        "training",
        help="Training data and fine-tuning.",
        description="Generate training data or fine-tune embeddings.",
    )
    _add_common_flags(training_parser)
    training_sub = training_parser.add_subparsers(dest="training_action")

    tr_gen = training_sub.add_parser("generate-data", help="Generate contrastive training triplets.")
    tr_gen.add_argument("output_path", help="Output JSONL file path.")

    tr_ft = training_sub.add_parser("fine-tune", help="Fine-tune BGE-M3 on training data.")
    tr_ft.add_argument("data_path", help="Training data JSONL file.")
    tr_ft.add_argument("--output-dir", default="models/fine-tuned", help="Model output directory.")
    tr_ft.add_argument("--epochs", type=int, default=3, help="Number of epochs (default: 3).")

    # ── admin ─────────────────────────────────────────────────────
    admin_parser = subparsers.add_parser(
        "admin",
        help="Administrative operations.",
        description="Reset index and other admin tasks.",
    )
    _add_common_flags(admin_parser)
    admin_sub = admin_parser.add_subparsers(dest="admin_action")

    admin_reset = admin_sub.add_parser("reset-index", help="Delete and recreate the email collection.")
    admin_reset.add_argument("--yes", action="store_true", help="Confirm the destructive operation.")

    return parser


# ── Legacy parser (backward compat) ──────────────────────────────


def _build_legacy_parser() -> argparse.ArgumentParser:
    """Build the legacy flat-flag parser (unchanged from original)."""
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Search your email archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python -m src.cli --query "invoice from vendor" --sender billing@vendor.com\n'
            '  python -m src.cli --query "security review" --format json\n'
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

    # Export & browse commands
    parser.add_argument(
        "--export-thread",
        metavar="CONVERSATION_ID",
        default=None,
        help="Export a conversation thread as HTML/PDF and exit.",
    )
    parser.add_argument(
        "--export-email",
        metavar="UID",
        default=None,
        help="Export a single email as HTML/PDF and exit.",
    )
    parser.add_argument(
        "--export-format",
        choices=["html", "pdf"],
        default="html",
        help="Format for --export-thread / --export-email (default: html).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output path for --export-thread / --export-email.",
    )
    parser.add_argument(
        "--browse",
        action="store_true",
        help="Browse emails in pages for systematic review.",
    )

    # Evidence commands
    parser.add_argument(
        "--evidence-list",
        action="store_true",
        help="List all evidence items (use --category and --min-relevance to filter).",
    )
    parser.add_argument(
        "--evidence-export",
        metavar="PATH",
        default=None,
        help="Export evidence report to file.",
    )
    parser.add_argument(
        "--evidence-export-format",
        choices=["html", "csv", "pdf"],
        default="html",
        help="Format for --evidence-export (default: html).",
    )
    parser.add_argument(
        "--evidence-stats",
        action="store_true",
        help="Show evidence collection statistics.",
    )
    parser.add_argument(
        "--evidence-verify",
        action="store_true",
        help="Re-verify all evidence quotes against source emails.",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Evidence category filter for --evidence-list / --evidence-export.",
    )
    parser.add_argument(
        "--min-relevance",
        type=int,
        choices=[1, 2, 3, 4, 5],
        default=None,
        help="Minimum relevance filter for --evidence-list / --evidence-export.",
    )
    # Chain of custody & dossier commands
    parser.add_argument(
        "--dossier",
        metavar="PATH",
        default=None,
        help="Generate proof dossier and write to file.",
    )
    parser.add_argument(
        "--dossier-format",
        choices=["html", "pdf"],
        default="html",
        help="Format for --dossier (default: html).",
    )
    parser.add_argument(
        "--custody-chain",
        action="store_true",
        help="View chain-of-custody audit trail and exit.",
    )
    parser.add_argument(
        "--provenance",
        metavar="UID",
        default=None,
        help="View email provenance (OLM source hash, ingestion run, custody events) and exit.",
    )

    # Fine-tuning commands
    parser.add_argument(
        "--generate-training-data",
        metavar="PATH",
        default=None,
        help="Generate contrastive training triplets as JSONL and exit.",
    )
    parser.add_argument(
        "--fine-tune",
        metavar="PATH",
        default=None,
        help="Fine-tune BGE-M3 on training data JSONL and exit.",
    )
    parser.add_argument(
        "--fine-tune-output",
        metavar="DIR",
        default=None,
        help="Output directory for fine-tuned model (default: models/fine-tuned).",
    )
    parser.add_argument(
        "--fine-tune-epochs",
        type=int,
        default=3,
        help="Number of fine-tuning epochs (default: 3).",
    )

    parser.add_argument(
        "--page",
        type=_positive_int_arg,
        default=1,
        help="Page number for --browse (default: 1).",
    )
    parser.add_argument(
        "--page-size",
        type=_positive_int_arg,
        default=20,
        help="Emails per page for --browse (default: 20, max: 50).",
    )

    return parser


# ── Legacy → subcommand inference ─────────────────────────────────


def _infer_subcommand(args: argparse.Namespace) -> str | None:
    """Map legacy flat-flag usage to the recommended subcommand.

    In addition to returning the subcommand name, this sets the
    ``*_action`` attribute that the subcommand handlers expect
    (e.g. ``args.analytics_action``).  Without this, legacy flags
    would always hit the ``else`` branch and ``sys.exit(2)``.
    """
    if getattr(args, "query", None) is not None:
        return "search"
    if getattr(args, "browse", False):
        return "browse"
    if any(
        getattr(args, f, None)
        for f in [
            "export_thread",
            "export_email",
            "generate_report",
            "export_network",
        ]
    ):
        return "export"

    # ── Evidence ──
    if getattr(args, "evidence_list", False):
        args.evidence_action = "list"
        return "evidence"
    if getattr(args, "evidence_export", None):
        args.evidence_action = "export"
        args.output_path = args.evidence_export
        return "evidence"
    if getattr(args, "evidence_stats", False):
        args.evidence_action = "stats"
        return "evidence"
    if getattr(args, "evidence_verify", False):
        args.evidence_action = "verify"
        return "evidence"
    if getattr(args, "dossier", None):
        args.evidence_action = "dossier"
        args.output_path = args.dossier
        return "evidence"
    if getattr(args, "custody_chain", False):
        args.evidence_action = "custody"
        return "evidence"
    if getattr(args, "provenance", None):
        args.evidence_action = "provenance"
        args.uid = args.provenance
        return "evidence"

    # ── Analytics ──
    if getattr(args, "stats", False):
        args.analytics_action = "stats"
        return "analytics"
    if getattr(args, "list_senders", False):
        args.analytics_action = "senders"
        return "analytics"
    if getattr(args, "suggest", False):
        args.analytics_action = "suggest"
        return "analytics"
    if getattr(args, "top_contacts", None):
        args.analytics_action = "contacts"
        if not hasattr(args, "email_address"):
            args.email_address = args.top_contacts
        return "analytics"
    if getattr(args, "volume", False):
        args.analytics_action = "volume"
        return "analytics"
    if getattr(args, "entities", None) is not None:
        args.analytics_action = "entities"
        if not hasattr(args, "entity_type"):
            args.entity_type = args.entities or None
        return "analytics"
    if getattr(args, "heatmap", False):
        args.analytics_action = "heatmap"
        return "analytics"
    if getattr(args, "response_times", False):
        args.analytics_action = "response-times"
        return "analytics"

    # ── Training ──
    if getattr(args, "generate_training_data", None):
        args.training_action = "generate-data"
        args.output_path = args.generate_training_data
        return "training"
    if getattr(args, "fine_tune", None):
        args.training_action = "fine-tune"
        args.data_path = args.fine_tune
        return "training"

    # ── Admin ──
    if getattr(args, "reset_index", False):
        args.admin_action = "reset-index"
        return "admin"

    return None  # interactive mode


# ── Subcommand detection ──────────────────────────────────────────

_SUBCOMMANDS = frozenset({"search", "browse", "export", "evidence", "analytics", "training", "admin"})


def _has_subcommand(argv: list[str] | None) -> bool:
    """Check whether argv starts with a known subcommand name.

    Only checks argv[0] — subcommands must come first.  Previous logic
    skipped flags and tested the first non-flag token, which broke when
    a flag value (e.g. ``--db-path /tmp/analytics``) happened to collide
    with a subcommand name.
    """
    if argv is None:
        argv = sys.argv[1:]
    return bool(argv) and argv[0] in _SUBCOMMANDS


# ── Unified parse_args ────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments — try subcommands first, fall back to legacy flags."""
    if _has_subcommand(argv):
        # Modern subcommand path
        new_parser = _build_subcommand_parser()
        args = new_parser.parse_args(argv)

        # Normalize search query: positional or --query
        if args.subcommand == "search":
            query_pos = getattr(args, "query_positional", None)
            query_flag = getattr(args, "query", None)
            if query_pos and query_flag:
                new_parser.error("Provide query as positional argument or --query, not both.")
            args.query = query_pos or query_flag
            if args.query is None:
                new_parser.error("search requires a query (positional or --query).")
            # Validate date window
            date_from = getattr(args, "date_from", None)
            date_to = getattr(args, "date_to", None)
            try:
                validate_date_window(date_from, date_to)
            except ValueError:
                new_parser.error("--date-from cannot be later than --date-to")
            # Validate --json + --format combo
            if getattr(args, "json", False) and getattr(args, "format", None) is not None:
                new_parser.error("--json cannot be combined with --format; use only --format {text,json}")
        return args

    # Legacy flat-flag parser
    legacy_parser = _build_legacy_parser()
    args = legacy_parser.parse_args(argv)
    inferred = _infer_subcommand(args)
    if inferred:
        args.subcommand = inferred
        warnings.warn(
            f"Flat-flag usage is deprecated. Use: python -m src.cli {inferred} ...",
            DeprecationWarning,
            stacklevel=2,
        )
    else:
        args.subcommand = None
    _validate_arg_combinations(args, legacy_parser)
    return args


# ── Validation (legacy parser) ───────────────────────────────────


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
        bool(args.top_contacts),
        bool(args.volume),
        args.entities is not None,
        bool(args.heatmap),
        bool(args.response_times),
    ]
    evidence_modes = [
        bool(args.evidence_list),
        args.evidence_export is not None,
        bool(args.evidence_stats),
        bool(args.evidence_verify),
    ]
    custody_modes = [
        args.dossier is not None,
        bool(args.custody_chain),
        bool(args.provenance),
    ]
    finetune_modes = [
        args.generate_training_data is not None,
        args.fine_tune is not None,
    ]
    operational_modes = [
        bool(args.stats),
        bool(args.list_senders),
        bool(args.reset_index),
        bool(args.suggest),
        args.generate_report is not None,
        args.export_network is not None,
        bool(args.export_thread),
        bool(args.export_email),
        bool(args.browse),
        *analytics_modes,
        *evidence_modes,
        *custody_modes,
        *finetune_modes,
    ]
    if sum(operational_modes) > 1:
        parser.error(
            "--stats, --list-senders, --reset-index, --suggest, --generate-report, "
            "--export-network, --export-thread, --export-email, --browse, "
            "--evidence-list, --evidence-export, --evidence-stats, --evidence-verify, "
            "--dossier, --custody-chain, --provenance, "
            "--top-contacts, --volume, "
            "--entities, --heatmap, and --response-times are mutually exclusive"
        )

    if args.query and any(operational_modes):
        parser.error(
            "--query cannot be combined with operational commands (--stats, --list-senders, --reset-index, --top-contacts, etc.)"
        )


# ── Main dispatch ────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    configure_logging(getattr(args, "log_level", None))

    try:
        from .retriever import EmailRetriever
    except ModuleNotFoundError as exc:
        print("Missing runtime dependency. Install project dependencies first:")
        print("  pip install -r requirements.txt")
        print(f"Details: {exc}")
        sys.exit(2)

    retriever = EmailRetriever(chromadb_path=getattr(args, "chromadb_path", None))

    _DISPATCH: dict[str | None, Any] = {
        "search": lambda: _cmd_search(args, retriever),
        "browse": lambda: _cmd_browse(args),
        "export": lambda: _cmd_export(args),
        "evidence": lambda: _cmd_evidence(args),
        "analytics": lambda: _cmd_analytics(args, retriever),
        "training": lambda: _cmd_training(args),
        "admin": lambda: _cmd_admin(args, retriever),
        None: lambda: _cmd_legacy(args, retriever),
    }

    handler = _DISPATCH.get(args.subcommand, _DISPATCH[None])
    handler()


if __name__ == "__main__":
    main()
