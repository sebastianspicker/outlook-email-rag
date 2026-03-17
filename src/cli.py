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
import json
import logging
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from dotenv import load_dotenv

from .config import configure_logging, get_settings
from .sanitization import sanitize_untrusted_text
from .validation import parse_iso_date, positive_int, score_float, validate_date_window

if TYPE_CHECKING:
    from .retriever import EmailRetriever

logger = logging.getLogger(__name__)
OutputFormat = Literal["text", "json"]


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
        "--email-type", choices=["reply", "forward", "original"], default=None,
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
        "--top-k", type=_top_k_int, default=settings.top_k,
        help="Number of results to retrieve.",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default=None,
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
            "  python -m src.cli search \"invoice from vendor\" --sender billing@vendor.com\n"
            "  python -m src.cli analytics stats\n"
            "  python -m src.cli export thread CONV_ID --format pdf\n"
            "\n"
            "Legacy flat-flag syntax is still supported but deprecated:\n"
            "  python -m src.cli --query \"invoice\" --sender billing@vendor.com\n"
        ),
    )
    parser.add_argument("--version", action="version", version="0.1.0")
    parser.add_argument("--chromadb-path", default=None, help="Custom ChromaDB path.")
    parser.add_argument("--log-level", default=None, help="Logging level override.")

    subparsers = parser.add_subparsers(dest="subcommand")

    # ── search ────────────────────────────────────────────────────
    search_parser = subparsers.add_parser(
        "search", help="Search emails with filters.",
        description="Search emails using natural language queries with optional metadata filters.",
    )
    _add_common_flags(search_parser)
    search_parser.add_argument(
        "query_positional", nargs="?", default=None, metavar="QUERY",
        help="Search query (alternative to --query).",
    )
    search_parser.add_argument("--query", "-q", default=None, help="Search query.")
    _add_search_filters(search_parser)

    # ── browse ────────────────────────────────────────────────────
    browse_parser = subparsers.add_parser(
        "browse", help="Browse emails in pages.",
        description="Browse all emails in paginated view for systematic review.",
    )
    _add_common_flags(browse_parser)
    browse_parser.add_argument("--page", type=_positive_int_arg, default=1, help="Page number (default: 1).")
    browse_parser.add_argument("--page-size", type=_positive_int_arg, default=20, help="Emails per page (default: 20, max: 50).")
    browse_parser.add_argument("--folder", default=None, help="Filter by folder.")
    browse_parser.add_argument("--sender", default=None, help="Filter by sender.")

    # ── export ────────────────────────────────────────────────────
    export_parser = subparsers.add_parser(
        "export", help="Export emails, threads, and reports.",
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
        "evidence", help="Evidence management, custody, and dossier.",
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
        "analytics", help="Statistics, contacts, volume, entities.",
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
        "period", nargs="?", choices=["day", "week", "month"], default="month",
        help="Time period (default: month).",
    )

    an_entities = analytics_sub.add_parser("entities", help="Show top entities.")
    an_entities.add_argument(
        "--type", dest="entity_type", default=None,
        help="Entity type filter (organization/url/phone/mention/email).",
    )

    analytics_sub.add_parser("heatmap", help="Show activity heatmap (hour × day-of-week).")
    analytics_sub.add_parser("response-times", help="Show average response times per replier.")

    # ── training ──────────────────────────────────────────────────
    training_parser = subparsers.add_parser(
        "training", help="Training data and fine-tuning.",
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
        "admin", help="Administrative operations.",
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
    if any(getattr(args, f, None) for f in [
        "export_thread", "export_email", "generate_report", "export_network",
    ]):
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
                new_parser.error(
                    "--json cannot be combined with --format; use only --format {text,json}"
                )
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
        bool(args.top_contacts), bool(args.volume), args.entities is not None,
        bool(args.heatmap), bool(args.response_times),
    ]
    evidence_modes = [
        bool(args.evidence_list), args.evidence_export is not None,
        bool(args.evidence_stats), bool(args.evidence_verify),
    ]
    custody_modes = [
        args.dossier is not None, bool(args.custody_chain),
        bool(args.provenance),
    ]
    finetune_modes = [
        args.generate_training_data is not None,
        args.fine_tune is not None,
    ]
    operational_modes = [
        bool(args.stats), bool(args.list_senders), bool(args.reset_index),
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
            "--query cannot be combined with operational commands "
            "(--stats, --list-senders, --reset-index, --top-contacts, etc.)"
        )


# ── Output format ────────────────────────────────────────────────


def resolve_output_format(args: argparse.Namespace) -> OutputFormat:
    if getattr(args, "format", None) is not None:
        return args.format
    if getattr(args, "json", False):
        logger.warning("--json is deprecated; use --format json")
        return "json"
    return "text"


# ── Interactive mode ─────────────────────────────────────────────


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


# ── Subcommand handlers ──────────────────────────────────────────


def _cmd_search(args: argparse.Namespace, retriever: "EmailRetriever") -> None:
    """Handle `search` subcommand."""
    output_format = resolve_output_format(args)
    code = run_single_query(
        retriever,
        query=args.query,
        as_json=(output_format == "json"),
        top_k=getattr(args, "top_k", 10),
        sender=getattr(args, "sender", None),
        subject=getattr(args, "subject", None),
        folder=getattr(args, "folder", None),
        cc=getattr(args, "cc", None),
        to=getattr(args, "to", None),
        bcc=getattr(args, "bcc", None),
        has_attachments=True if getattr(args, "has_attachments", None) else None,
        priority=getattr(args, "priority", None),
        email_type=getattr(args, "email_type", None),
        date_from=getattr(args, "date_from", None),
        date_to=getattr(args, "date_to", None),
        min_score=getattr(args, "min_score", None),
        rerank=getattr(args, "rerank", False),
        hybrid=getattr(args, "hybrid", False),
        topic_id=getattr(args, "topic", None),
        cluster_id=getattr(args, "cluster_id", None),
        expand_query=getattr(args, "expand_query", False),
    )
    sys.exit(code)


def _cmd_browse(args: argparse.Namespace) -> None:
    """Handle `browse` subcommand."""
    page_size = min(args.page_size, 50)
    offset = (args.page - 1) * page_size
    _run_browse(
        offset=offset,
        limit=page_size,
        folder=getattr(args, "folder", None),
        sender=getattr(args, "sender", None),
    )
    sys.exit(0)


def _cmd_export(args: argparse.Namespace) -> None:
    """Handle `export` subcommand."""
    action = getattr(args, "export_action", None)
    if action == "thread":
        _run_export_thread(args.conversation_id, args.format, getattr(args, "output", None))
    elif action == "email":
        _run_export_email(args.uid, args.format, getattr(args, "output", None))
    elif action == "report":
        _run_generate_report(getattr(args, "output", "report.html"))
    elif action == "network":
        _run_export_network(getattr(args, "output", "network.graphml"))
    else:
        print("Usage: python -m src.cli export {thread,email,report,network}")
        sys.exit(2)
    sys.exit(0)


def _cmd_evidence(args: argparse.Namespace) -> None:
    """Handle `evidence` subcommand."""
    action = getattr(args, "evidence_action", None)
    if action == "list":
        _run_evidence_list(
            getattr(args, "category", None),
            getattr(args, "min_relevance", None),
        )
    elif action == "export":
        _run_evidence_export(
            args.output_path,
            getattr(args, "format", "html"),
            getattr(args, "category", None),
            getattr(args, "min_relevance", None),
        )
    elif action == "stats":
        _run_evidence_stats()
    elif action == "verify":
        _run_evidence_verify()
    elif action == "dossier":
        _run_dossier(
            args.output_path,
            getattr(args, "format", "html"),
            getattr(args, "category", None),
            getattr(args, "min_relevance", None),
        )
    elif action == "custody":
        _run_custody_chain()
    elif action == "provenance":
        _run_provenance(args.uid)
    else:
        print("Usage: python -m src.cli evidence {list,export,stats,verify,dossier,custody,provenance}")
        sys.exit(2)
    sys.exit(0)


def _cmd_analytics(args: argparse.Namespace, retriever: "EmailRetriever") -> None:
    """Handle `analytics` subcommand."""
    action = getattr(args, "analytics_action", None)
    if action == "stats":
        print(json.dumps(retriever.stats(), indent=2))
    elif action == "senders":
        limit = getattr(args, "limit", 30)
        _print_sender_lines(retriever.list_senders(limit), print_fn=print)
    elif action == "suggest":
        _run_suggest()
    elif action == "contacts":
        db = _get_email_db()
        _run_top_contacts(db, args.email_address)
    elif action == "volume":
        db = _get_email_db()
        _run_volume(db, getattr(args, "period", "month"))
    elif action == "entities":
        db = _get_email_db()
        _run_entities(db, getattr(args, "entity_type", None))
    elif action == "heatmap":
        db = _get_email_db()
        _run_heatmap(db)
    elif action == "response-times":
        db = _get_email_db()
        _run_response_times(db)
    else:
        print("Usage: python -m src.cli analytics {stats,senders,suggest,contacts,volume,entities,heatmap,response-times}")
        sys.exit(2)
    sys.exit(0)


def _cmd_training(args: argparse.Namespace) -> None:
    """Handle `training` subcommand."""
    action = getattr(args, "training_action", None)
    if action == "generate-data":
        _run_generate_training_data(args.output_path)
    elif action == "fine-tune":
        _run_fine_tune(
            args.data_path,
            output_dir=getattr(args, "output_dir", "models/fine-tuned"),
            epochs=getattr(args, "epochs", 3),
        )
    else:
        print("Usage: python -m src.cli training {generate-data,fine-tune}")
        sys.exit(2)
    sys.exit(0)


def _cmd_admin(args: argparse.Namespace, retriever: "EmailRetriever") -> None:
    """Handle `admin` subcommand."""
    action = getattr(args, "admin_action", None)
    if action == "reset-index":
        if not getattr(args, "yes", False):
            print("Refusing to reset index without --yes.")
            sys.exit(2)
        retriever.reset_index()
        print("Index has been reset.")
    else:
        print("Usage: python -m src.cli admin {reset-index}")
        sys.exit(2)
    sys.exit(0)


def _cmd_legacy(args: argparse.Namespace, retriever: "EmailRetriever") -> None:
    """Handle legacy flat-flag dispatch (no subcommand detected)."""
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

    # Export commands
    if args.export_thread:
        _run_export_thread(args.export_thread, args.export_format, args.output)
        sys.exit(0)

    if args.export_email:
        _run_export_email(args.export_email, args.export_format, args.output)
        sys.exit(0)

    # Browse command
    if args.browse:
        page_size = min(args.page_size, 50)
        offset = (args.page - 1) * page_size
        _run_browse(
            offset=offset,
            limit=page_size,
            folder=args.folder,
            sender=args.sender,
        )
        sys.exit(0)

    # Evidence commands
    if args.evidence_list:
        _run_evidence_list(args.category, args.min_relevance)
        sys.exit(0)

    if args.evidence_export:
        _run_evidence_export(args.evidence_export, args.evidence_export_format,
                             args.category, args.min_relevance)
        sys.exit(0)

    if args.evidence_stats:
        _run_evidence_stats()
        sys.exit(0)

    if args.evidence_verify:
        _run_evidence_verify()
        sys.exit(0)

    # Chain of custody & dossier commands
    if args.dossier:
        _run_dossier(args.dossier, args.dossier_format, args.category, args.min_relevance)
        sys.exit(0)

    if args.custody_chain:
        _run_custody_chain()
        sys.exit(0)

    if args.provenance:
        _run_provenance(args.provenance)
        sys.exit(0)

    # Fine-tuning commands
    if args.generate_training_data:
        _run_generate_training_data(args.generate_training_data)
        sys.exit(0)

    if args.fine_tune:
        _run_fine_tune(
            args.fine_tune,
            output_dir=args.fine_tune_output or "models/fine-tuned",
            epochs=args.fine_tune_epochs,
        )
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


# ── Printing helpers ─────────────────────────────────────────────


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


# ── Database helper ──────────────────────────────────────────────


def _get_email_db():
    """Get EmailDatabase instance from settings, or exit with error."""
    settings = get_settings()
    sqlite_path = settings.sqlite_path
    if not sqlite_path or not Path(sqlite_path).exists():
        print("SQLite database not found. Run ingestion first:")
        print("  python -m src.ingest data/your-export.olm --extract-entities")
        sys.exit(1)

    from .email_db import EmailDatabase

    return EmailDatabase(sqlite_path)


# ── Run functions (unchanged domain logic) ───────────────────────


def _run_analytics_command(args: argparse.Namespace) -> None:
    """Dispatch analytics commands (legacy path)."""
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
        bar = "\u2588" * min(50, row["count"])
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
    print("\nActivity heatmap (hour \u00d7 day-of-week):\n")
    print(f"      {'   '.join(days)}")
    levels = " \u2591\u2592\u2593\u2588"
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
    print(f"\n  Legend: ' '=0  \u2591=low  \u2592=mid  \u2593=high  \u2588=peak (max={max_count})")


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
        print(f"  \u2022 {suggestion}")


def _run_generate_report(output_path: str) -> None:
    db = _get_email_db()
    from .report_generator import ReportGenerator

    generator = ReportGenerator(db)
    generator.generate(output_path=output_path)
    print(f"Report generated: {output_path}")


def _run_export_thread(conversation_id: str, fmt: str, output_path: str | None) -> None:
    db = _get_email_db()
    from .email_exporter import EmailExporter

    exporter = EmailExporter(db)
    if output_path:
        result = exporter.export_thread_file(conversation_id, output_path, fmt=fmt)
    else:
        # Default output path
        safe_id = conversation_id[:20].replace("/", "_")
        default_path = f"thread_{safe_id}.{fmt}"
        result = exporter.export_thread_file(conversation_id, default_path, fmt=fmt)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print(f"Thread exported: {result['output_path']} ({result['email_count']} emails)")
    if "note" in result:
        print(f"  Note: {result['note']}")


def _run_export_email(uid: str, fmt: str, output_path: str | None) -> None:
    db = _get_email_db()
    from .email_exporter import EmailExporter

    exporter = EmailExporter(db)
    if not output_path:
        output_path = f"email_{uid[:12]}.{fmt}"
    result = exporter.export_single_file(uid, output_path, fmt=fmt)
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print(f"Email exported: {result['output_path']}")
    if "note" in result:
        print(f"  Note: {result['note']}")


def _run_browse(
    offset: int = 0,
    limit: int = 20,
    folder: str | None = None,
    sender: str | None = None,
) -> None:
    db = _get_email_db()
    page = db.list_emails_paginated(
        offset=offset, limit=limit, folder=folder, sender=sender
    )
    total = page["total"]
    emails = page["emails"]
    page_num = (offset // limit) + 1
    total_pages = (total + limit - 1) // limit if total > 0 else 0

    print(f"\nBrowsing emails: page {page_num}/{total_pages} ({total} total)\n")
    if not emails:
        print("No emails found.")
        return

    for i, email in enumerate(emails, start=offset + 1):
        subj = sanitize_untrusted_text(str(email.get("subject", "(no subject)")))
        sender_val = email.get("sender_email", "?")
        date_val = str(email.get("date", "?"))[:10]
        uid = email.get("uid", "?")[:12]
        print(f"  {i:>4}  {date_val}  {sender_val:<30}  {subj}")
        print(f"        uid: {uid}  conv: {email.get('conversation_id', '')[:20]}")

    print(f"\nShowing {offset + 1}\u2013{offset + len(emails)} of {total}")
    if offset + limit < total:
        print(f"Next page: --browse --page {page_num + 1} --page-size {limit}")


def _run_evidence_list(category: str | None, min_relevance: int | None) -> None:
    db = _get_email_db()
    result = db.list_evidence(category=category, min_relevance=min_relevance)
    items = result["items"]
    total = result["total"]
    if not items:
        print("No evidence items found.")
        return
    print(f"\nEvidence items ({total} total):\n")
    for item in items:
        verified = "V" if item.get("verified") else "?"
        stars = "*" * item.get("relevance", 0)
        date_val = str(item.get("date", ""))[:10]
        cat = item.get("category", "")
        sender = item.get("sender_name") or item.get("sender_email", "?")
        quote_preview = item.get("key_quote", "")[:60]
        if len(item.get("key_quote", "")) > 60:
            quote_preview += "..."
        print(f"  [{item['id']:>4}] {date_val}  [{verified}] {stars:<5}  {cat:<20}  {sender}")
        print(f"         \"{quote_preview}\"")


def _run_evidence_export(
    output_path: str, fmt: str,
    category: str | None, min_relevance: int | None,
) -> None:
    db = _get_email_db()
    from .evidence_exporter import EvidenceExporter

    exporter = EvidenceExporter(db)
    result = exporter.export_file(
        output_path=output_path, fmt=fmt,
        min_relevance=min_relevance, category=category,
    )
    print(f"Evidence report exported: {result['output_path']} ({result['item_count']} items, {result['format']})")
    if "note" in result:
        print(f"  Note: {result['note']}")


def _run_evidence_stats() -> None:
    db = _get_email_db()
    stats = db.evidence_stats()
    print(json.dumps(stats, indent=2))


def _run_evidence_verify() -> None:
    db = _get_email_db()
    result = db.verify_evidence_quotes()
    print(f"\nVerification complete: {result['verified']} verified, {result['failed']} failed")
    if result["failures"]:
        print("\nFailed quotes:")
        for f in result["failures"]:
            print(f"  ID {f['evidence_id']}: \"{f['key_quote_preview']}\" (email: {f['email_uid'][:12]})")


def _run_dossier(
    output_path: str, fmt: str,
    category: str | None, min_relevance: int | None,
) -> None:
    db = _get_email_db()
    from .dossier_generator import DossierGenerator

    gen = DossierGenerator(db)
    result = gen.generate_file(
        output_path=output_path, fmt=fmt,
        category=category, min_relevance=min_relevance,
    )
    print(f"Dossier generated: {result['output_path']} ({result['evidence_count']} evidence items, {result['format']})")
    print(f"  SHA-256: {result['dossier_hash']}")


def _run_custody_chain() -> None:
    db = _get_email_db()
    events = db.get_custody_chain(limit=100)
    if not events:
        print("No custody events recorded.")
        return
    print(f"\nChain-of-custody audit trail ({len(events)} events):\n")
    for event in events:
        target = f"{event.get('target_type', '')}:{event.get('target_id', '')}" if event.get("target_type") else ""
        hash_preview = (event.get("content_hash") or "")[:16]
        print(f"  {event['timestamp']}  {event['action']:<20}  {event.get('actor', 'system'):<10}  {target}")
        if hash_preview:
            print(f"    hash: {hash_preview}...")


def _run_provenance(email_uid: str) -> None:
    db = _get_email_db()
    result = db.email_provenance(email_uid)
    print(json.dumps(result, indent=2, default=str))


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


def _run_generate_training_data(output_path: str) -> None:
    db = _get_email_db()
    from .training_data_generator import TrainingDataGenerator

    gen = TrainingDataGenerator(db)
    result = gen.export_jsonl(output_path)
    print(f"Training data generated: {output_path} ({result['triplet_count']} triplets)")


def _run_fine_tune(data_path: str, output_dir: str, epochs: int) -> None:
    from .fine_tuner import FineTuner

    ft = FineTuner()
    result = ft.fine_tune(
        training_data_path=data_path,
        output_dir=output_dir,
        epochs=epochs,
    )
    print(f"Fine-tuning result: {result['status']}")
    print(f"  Triplets: {result['triplet_count']}, Epochs: {result['epochs']}")
    if result.get("config_path"):
        print(f"  Config: {result['config_path']}")


if __name__ == "__main__":
    main()
