"""Shared modern CLI parser construction helpers."""

from __future__ import annotations

import argparse

from .config import get_settings
from .validation import parse_iso_date, positive_int, score_float


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


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by all subcommands."""
    parser.add_argument("--chromadb-path", default=None, help="Custom ChromaDB path.")
    parser.add_argument("--sqlite-path", default=None, help="Custom SQLite metadata path.")
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
    parser.add_argument("--sqlite-path", default=None, help="Custom SQLite metadata path.")
    parser.add_argument("--log-level", default=None, help="Logging level override.")

    subparsers = parser.add_subparsers(dest="subcommand")

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

    case_parser = subparsers.add_parser(
        "case",
        help="Exploratory and exhaustive workplace case workflows.",
        description=(
            "Run exploratory case analysis from structured JSON, or use the bounded "
            "prompt-preflight/full-pack/counsel-pack workflows for manifest-backed matter review."
        ),
    )
    _add_common_flags(case_parser)
    case_sub = case_parser.add_subparsers(dest="case_action")

    case_analyze = case_sub.add_parser(
        "analyze",
        help="Run exploratory retrieval-bounded analysis from a structured case JSON file.",
        description=(
            "Exploratory/raw-input workflow. For bounded prompt intake and manifest-backed "
            "counsel-facing matter review, prefer `case prompt-preflight`, `case full-pack`, "
            "or `case counsel-pack`."
        ),
    )
    case_analyze.add_argument("--input", required=True, help="Path to the case-analysis JSON input file.")
    case_analyze.add_argument("--output", "-o", default=None, help="Optional output file path.")
    case_analyze.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="CLI output format. Current implementation returns JSON for both values.",
    )

    case_prompt_preflight = case_sub.add_parser(
        "prompt-preflight",
        help="Draft a structured intake from a natural-language matter prompt.",
    )
    case_prompt_preflight.add_argument(
        "--input",
        required=True,
        help="Path to a text or markdown file containing the natural-language matter prompt.",
    )
    case_prompt_preflight.add_argument("--output", "-o", default=None, help="Optional output file path.")
    case_prompt_preflight.add_argument(
        "--output-language",
        choices=["en", "de"],
        default="en",
        help="Narrative output language for the prompt-preflight payload.",
    )

    case_full_pack = case_sub.add_parser(
        "full-pack",
        help="Compile a blocked-or-ready full-pack draft from a matter prompt and materials directory.",
    )
    case_full_pack.add_argument(
        "--prompt",
        required=True,
        help="Path to a text or markdown file containing the natural-language matter prompt.",
    )
    case_full_pack.add_argument("--materials-dir", required=True, help="Directory containing supplied matter files.")
    case_full_pack.add_argument(
        "--overrides",
        default=None,
        help="Optional path to a JSON object with structured intake overrides such as case_scope.trigger_events.",
    )
    case_full_pack.add_argument(
        "--output",
        "-o",
        default=None,
        help="Optional export artifact path. Omit to return the full-pack payload without writing an artifact.",
    )
    case_full_pack.add_argument(
        "--output-language",
        choices=["en", "de"],
        default="en",
        help="Narrative output language for the compiled legal-support input.",
    )
    case_full_pack.add_argument(
        "--translation-mode",
        choices=["source_only", "translation_aware"],
        default="translation_aware",
        help="Whether the compiled legal-support input should preserve explicit translation-aware rendering.",
    )
    case_full_pack.add_argument(
        "--default-source-scope",
        choices=["emails_only", "emails_and_attachments", "mixed_case_file"],
        default="emails_and_attachments",
        help="Fallback source scope when the prompt does not clearly describe mixed-source material.",
    )
    case_full_pack.add_argument(
        "--no-assume-date-to-today",
        dest="assume_date_to_today",
        action="store_false",
        help="Keep open-ended prompt ranges unresolved instead of drafting date_to as today's date.",
    )
    case_full_pack.add_argument(
        "--privacy-mode",
        choices=["full_access", "external_counsel_export", "internal_complaint_use", "witness_sharing"],
        default="external_counsel_export",
        help="Privacy mode for the downstream legal-support run and optional export.",
    )
    case_full_pack.add_argument(
        "--delivery-target",
        choices=["counsel_handoff", "counsel_handoff_bundle", "exhibit_register", "dashboard"],
        default="counsel_handoff_bundle",
        help="Which artifact to export when --output is provided.",
    )
    case_full_pack.add_argument(
        "--delivery-format",
        choices=["html", "pdf", "json", "csv", "bundle"],
        default="bundle",
        help="Export format to use when --output is provided.",
    )
    case_full_pack.add_argument(
        "--compile-only",
        action="store_true",
        help="Stop after blocker/ready compilation without running the downstream exhaustive workflow.",
    )
    case_full_pack.add_argument(
        "--allow-blocked-exit-zero",
        action="store_true",
        help="Keep exit code 0 for blocked full-pack runs while still emitting the blocked JSON payload.",
    )
    case_full_pack.set_defaults(assume_date_to_today=True)

    case_counsel_pack = case_sub.add_parser(
        "counsel-pack",
        help="Build a manifest-backed counsel pack from a case scope file and materials directory.",
    )
    case_counsel_pack.add_argument("--case-scope", required=True, help="Path to a JSON file containing the case_scope object.")
    case_counsel_pack.add_argument("--materials-dir", required=True, help="Directory containing supplied matter files.")
    case_counsel_pack.add_argument("--output", "-o", required=True, help="Output artifact path for the generated counsel pack.")
    case_counsel_pack.add_argument(
        "--delivery-target",
        choices=["counsel_handoff", "counsel_handoff_bundle", "exhibit_register", "dashboard"],
        default="counsel_handoff_bundle",
        help="Which export artifact to produce.",
    )
    case_counsel_pack.add_argument(
        "--delivery-format",
        choices=["html", "pdf", "json", "csv", "bundle"],
        default="bundle",
        help="Delivery format for the counsel pack export.",
    )
    case_counsel_pack.add_argument(
        "--privacy-mode",
        choices=["full_access", "external_counsel_export", "internal_complaint_use", "witness_sharing"],
        default="external_counsel_export",
        help="Privacy mode for the generated artifact.",
    )
    case_counsel_pack.add_argument("--output-language", choices=["en", "de"], default="en", help="Narrative output language.")
    case_counsel_pack.add_argument(
        "--translation-mode",
        choices=["source_only", "translation_aware"],
        default="translation_aware",
        help="Whether to keep explicit translation-aware evidence rendering.",
    )
    case_counsel_pack.add_argument(
        "--allow-blocked-exit-zero",
        action="store_true",
        help="Keep exit code 0 for blocked counsel-pack runs while still emitting the blocked JSON payload.",
    )

    case_review_status = case_sub.add_parser(
        "review-status",
        help="Inspect persisted snapshot and override state for one matter workspace.",
    )
    case_review_status.add_argument("--workspace-id", required=True, help="Persisted matter workspace ID.")

    case_review_override = case_sub.add_parser(
        "review-override",
        help="Persist one human review override for a reviewable matter item.",
    )
    case_review_override.add_argument("--workspace-id", required=True, help="Persisted matter workspace ID.")
    case_review_override.add_argument(
        "--target-type",
        required=True,
        choices=[
            "actor_link",
            "chronology_entry",
            "issue_tag_assignment",
            "exhibit_description",
            "contradiction_judgment",
        ],
        help="Reviewable target family to update.",
    )
    case_review_override.add_argument("--target-id", required=True, help="Stable item ID within the target family.")
    case_review_override.add_argument(
        "--review-state",
        required=True,
        choices=["machine_extracted", "human_verified", "disputed", "draft_only", "export_approved"],
        help="Human review state to persist for this item.",
    )
    case_review_override.add_argument(
        "--override-json",
        default=None,
        help="Path to a JSON object with the human-approved outward payload override.",
    )
    case_review_override.add_argument(
        "--machine-json",
        default=None,
        help="Path to a JSON object capturing the original machine payload for audit comparison.",
    )
    case_review_override.add_argument(
        "--source-evidence-json",
        default=None,
        help="Path to a JSON array of source-evidence anchors supporting the override.",
    )
    case_review_override.add_argument("--reviewer", default="human", help="Reviewer label for the persisted override.")
    case_review_override.add_argument("--review-notes", default="", help="Free-text review notes.")
    case_review_override.add_argument(
        "--no-apply-on-refresh",
        action="store_true",
        help="Store the override without automatically reapplying it on later case-analysis refreshes.",
    )

    case_review_snapshot = case_sub.add_parser(
        "review-snapshot",
        help="Update the persisted review_state for one matter snapshot.",
    )
    case_review_snapshot.add_argument("--snapshot-id", required=True, help="Persisted snapshot ID to update.")
    case_review_snapshot.add_argument(
        "--review-state",
        required=True,
        choices=["machine_extracted", "human_verified", "disputed", "draft_only", "export_approved", "superseded"],
        help="Review state to persist for the snapshot.",
    )
    case_review_snapshot.add_argument("--reviewer", default="human", help="Reviewer label for the snapshot transition.")

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
