"""Command handlers and helpers for the Email RAG CLI.

Extracted from cli.py to keep each module under 800 lines.
All functions here are imported and re-exported by cli.py so that
existing imports (``from src.cli import _cmd_search``) keep working.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .config import get_settings
from .sanitization import sanitize_untrusted_text

if TYPE_CHECKING:
    from .retriever import EmailRetriever

logger = logging.getLogger(__name__)
OutputFormat = Literal["text", "json"]


# ── Output format ────────────────────────────────────────────────


def resolve_output_format(args: argparse.Namespace) -> OutputFormat:
    if getattr(args, "format", None) is not None:
        return args.format
    if getattr(args, "json", False):
        logger.warning("--json is deprecated; use --format json")
        return "json"
    return "text"


# ── Interactive mode ─────────────────────────────────────────────


def run_interactive(retriever: EmailRetriever, top_k: int = 10) -> None:
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
    retriever: EmailRetriever,
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
        _print_rich_or_plain(
            rich_fn=lambda c: (
                c.print("[yellow]No matching emails found.[/]"),
                c.print("[dim]Try refining query terms, sender filter, or date window.[/]"),
            ),
            plain_fn=lambda: (
                print("No matching emails found."),
                print("Try refining query terms, sender filter, or date window."),
            ),
        )
        return 0

    _print_rich_or_plain(
        rich_fn=lambda c: _render_single_query_rich(c, query, results),
        plain_fn=lambda: _render_single_query_plain(query, results),
    )
    return 0


def _print_rich_or_plain(rich_fn, plain_fn) -> None:
    """Try rich output, fall back to plain."""
    try:
        from rich.console import Console

        console = Console()
        rich_fn(console)
    except ImportError:
        plain_fn()


def _render_single_query_rich(console, query: str, results) -> None:
    """Render single query results with rich formatting."""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    # Summary header
    scores = [float(r.score) for r in results]
    avg = sum(scores) / len(scores) if scores else 0.0
    console.print(
        Panel(
            f'[bold]{len(results)}[/] results for [cyan]"{query}"[/]  |  '
            f"Best: [green]{max(scores):.0%}[/]  Avg: {avg:.0%}  Lowest: {min(scores):.0%}",
            title="[bold]Search Results[/]",
            border_style="blue",
        )
    )

    # Results table
    table = Table(show_lines=True, border_style="dim")
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Score", width=7, justify="center")
    table.add_column("Date", width=10)
    table.add_column("Sender", width=25, no_wrap=True)
    table.add_column("Subject", min_width=30)
    table.add_column("Folder", width=12, style="dim")

    for i, r in enumerate(results, 1):
        m = r.metadata
        score_val = float(r.score)
        score_style = "green bold" if score_val >= 0.75 else ("yellow" if score_val >= 0.45 else "red")
        sender_val = sanitize_untrusted_text(str(m.get("sender_name") or m.get("sender_email", "?")))
        subj_val = sanitize_untrusted_text(str(m.get("subject", "(no subject)")))
        date_val = sanitize_untrusted_text(str(m.get("date", "?"))[:10])
        folder_val = sanitize_untrusted_text(str(m.get("folder", "")))

        table.add_row(
            str(i),
            Text(f"{score_val:.0%}", style=score_style),
            date_val,
            sender_val,
            subj_val,
            folder_val,
        )

    console.print(table)

    # Detailed previews
    for i, r in enumerate(results, 1):
        m = r.metadata
        score_val = float(r.score)
        score_style = "green" if score_val >= 0.75 else ("yellow" if score_val >= 0.45 else "red")
        subj = sanitize_untrusted_text(str(m.get("subject", "(no subject)")))
        sender = sanitize_untrusted_text(str(m.get("sender_name") or m.get("sender_email", "?")))
        date_v = sanitize_untrusted_text(str(m.get("date", "?"))[:10])
        uid_short = str(m.get("uid", ""))[:12]
        email_type = m.get("email_type", "")
        type_label = f"  [dim]\\[{email_type}][/]" if email_type and email_type != "original" else ""
        body = sanitize_untrusted_text(str(r.text or ""))
        preview = body[:600] + "..." if len(body) > 600 else body

        header = (
            f"[{score_style}]{score_val:.0%}[/]  [bold]{subj}[/]{type_label}\n"
            f"{sender}  |  {date_v}  |  [dim]UID: {uid_short}[/]"
        )
        console.print(
            Panel(
                f"{header}\n\n[dim]{preview}[/]",
                title=f"[dim]Result {i}[/]",
                border_style="dim",
                padding=(0, 1),
            )
        )


def _render_single_query_plain(query: str, results) -> None:
    """Plain-text fallback for single query results."""
    scores = [float(r.score) for r in results]
    avg = sum(scores) / len(scores) if scores else 0.0
    print(f'\n  {len(results)} results for "{query}"')
    print(f"  Best: {max(scores):.0%}  Avg: {avg:.0%}  Lowest: {min(scores):.0%}")
    print()

    for i, r in enumerate(results, 1):
        m = r.metadata
        print(f"{'=' * 70}")
        print(f"  [{i}] {r.score:.0%}  {m.get('subject', '(no subject)')}")
        sender = m.get("sender_name") or m.get("sender_email", "?")
        date = str(m.get("date", "?"))[:10]
        folder = m.get("folder", "")
        print(f"  From: {sender}  |  {date}  |  {folder}")
        body = sanitize_untrusted_text(str(r.text or ""))
        preview = body[:600] + "..." if len(body) > 600 else body
        print(f"\n  {preview}\n")


# ── Subcommand handlers ──────────────────────────────────────────


def _cmd_search(args: argparse.Namespace, retriever: EmailRetriever) -> None:
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
        return  # unreachable; satisfies static analysis
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


def _cmd_analytics(args: argparse.Namespace, retriever: EmailRetriever) -> None:
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


def _cmd_admin(args: argparse.Namespace, retriever: EmailRetriever) -> None:
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


def _cmd_legacy(args: argparse.Namespace, retriever: EmailRetriever) -> None:
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
        _run_evidence_export(args.evidence_export, args.evidence_export_format, args.category, args.min_relevance)
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


# ── Printing helpers ─────────────────────────────────────────────


def _print_sender_lines(senders: list[dict[str, Any]], print_fn=print) -> None:
    if not senders:
        print_fn("No senders found.")
        return

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="[bold]Top Senders[/]", border_style="dim", show_lines=False)
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Count", width=6, justify="right", style="cyan bold")
        table.add_column("Name", min_width=20)
        table.add_column("Email", style="dim")

        for i, sender in enumerate(senders, 1):
            safe_name = sanitize_untrusted_text(str(sender["name"] or "(unknown)"))
            safe_email = sanitize_untrusted_text(str(sender["email"]))
            table.add_row(str(i), f"{sender['count']:,}", safe_name, safe_email)
        console.print(table)
    except ImportError:
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


def _render_interactive_intro(console, panel_cls, retriever: EmailRetriever) -> None:
    stats = retriever.stats()
    total = stats.get("total_emails", 0)
    chunks = stats.get("total_chunks", 0)
    senders = stats.get("unique_senders", 0)
    dr = stats.get("date_range", {})
    earliest = dr.get("earliest", "?")
    latest = dr.get("latest", "?")

    console.print(
        panel_cls(
            f"  [bold]{total:,}[/] emails  |  [bold]{chunks:,}[/] chunks  |  "
            f"[bold]{senders:,}[/] unique senders\n"
            f"  Date range: {earliest} to {latest}",
            title="[bold blue]Email RAG -- Discovery & Investigation[/]",
            subtitle="[dim]'quit' to exit  |  'stats' for details  |  'senders' to list top senders[/]",
            border_style="blue",
            padding=(1, 2),
        )
    )


def _render_stats(console, retriever: EmailRetriever) -> None:
    from rich.panel import Panel
    from rich.table import Table

    stats = retriever.stats()
    total = stats.get("total_emails", 0)
    chunks = stats.get("total_chunks", 0)
    senders = stats.get("unique_senders", 0)
    dr = stats.get("date_range", {})
    earliest = dr.get("earliest", "?")
    latest = dr.get("latest", "?")

    summary = (
        f"  [bold]{total:,}[/] emails  |  [bold]{chunks:,}[/] chunks  |  "
        f"[bold]{senders:,}[/] unique senders\n"
        f"  Date range: {earliest} to {latest}"
    )
    console.print(Panel(summary, title="[bold blue]Archive Statistics[/]", border_style="blue"))

    folders = stats.get("folders", {})
    if folders:
        table = Table(title="[bold]Folders[/]", border_style="dim")
        table.add_column("Folder", min_width=20)
        table.add_column("Count", justify="right", style="cyan bold")
        for name, count in sorted(folders.items(), key=lambda x: x[1], reverse=True):
            table.add_row(name, f"{count:,}")
        console.print(table)


def _render_senders(console, retriever: EmailRetriever) -> None:
    _print_sender_lines(retriever.list_senders(30), print_fn=console.print)


def _render_results_table(console, table_cls, results) -> None:
    from rich.text import Text

    table = table_cls(
        title=f"[bold]{len(results)} results[/]",
        show_lines=True,
        border_style="dim",
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Score", width=7, justify="center")
    table.add_column("Date", width=10)
    table.add_column("Sender", width=25, no_wrap=True)
    table.add_column("Subject", min_width=30)
    table.add_column("Folder", width=12, style="dim")

    for index, result in enumerate(results[:10], 1):
        metadata = result.metadata
        score_val = float(result.score)
        score_style = "green bold" if score_val >= 0.75 else ("yellow" if score_val >= 0.45 else "red")
        subject = sanitize_untrusted_text(str(metadata.get("subject", "(no subject)")))
        sender_value = metadata.get("sender_name") or metadata.get("sender_email", "?")
        sender = sanitize_untrusted_text(str(sender_value))
        date_value = sanitize_untrusted_text(str(metadata.get("date", "?"))[:10])
        folder_val = sanitize_untrusted_text(str(metadata.get("folder", "")))
        table.add_row(
            str(index),
            Text(f"{score_val:.0%}", style=score_style),
            date_value,
            sender,
            subject,
            folder_val,
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

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title=f"[bold]Top Contacts for {email_address}[/]", border_style="dim")
        table.add_column("#", style="dim", width=3, justify="right")
        table.add_column("Emails", width=8, justify="right", style="cyan bold")
        table.add_column("Contact", min_width=30)

        max_count = max(c["total"] for c in contacts) if contacts else 1
        for i, contact in enumerate(contacts, 1):
            bar_len = int((contact["total"] / max_count) * 20) if max_count else 0
            bar = "\u2588" * bar_len
            table.add_row(str(i), f"{contact['total']:,}", f"{contact['partner']}  [dim]{bar}[/]")
        console.print(table)
    except ImportError:
        print(f"\nTop contacts for {email_address}:\n")
        for contact in contacts:
            print(f"  {contact['total']:>4}x  {contact['partner']}")


def _run_volume(db, period: str) -> None:
    from .temporal_analysis import TemporalAnalyzer

    analyzer = TemporalAnalyzer(db)
    data = analyzer.volume_over_time(period=period)
    if not data:
        print("No volume data available.")
        return

    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        max_count = max(row["count"] for row in data) if data else 1
        total = sum(row["count"] for row in data)

        lines: list[str] = []
        for row in data:
            bar_len = int((row["count"] / max_count) * 40) if max_count else 0
            bar = "\u2588" * bar_len
            count_str = f"{row['count']:>5}"
            lines.append(f"  {row['period']}  {count_str}  [cyan]{bar}[/]")

        body = "\n".join(lines)
        console.print(
            Panel(
                body,
                title=f"[bold]Email Volume by {period}[/]",
                subtitle=f"[dim]{total:,} total emails[/]",
                border_style="blue",
            )
        )
    except ImportError:
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
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title=f"[bold]Top Entities ({label})[/]", border_style="dim")
        table.add_column("#", style="dim", width=3, justify="right")
        table.add_column("Mentions", width=9, justify="right", style="cyan bold")
        table.add_column("Type", width=14)
        table.add_column("Entity", min_width=25)

        type_styles = {
            "organization": "bold magenta",
            "person": "bold green",
            "url": "blue underline",
            "phone": "yellow",
            "email": "cyan",
            "event": "red",
        }
        for i, ent in enumerate(entities, 1):
            etype = ent["entity_type"]
            style = type_styles.get(etype, "")
            table.add_row(
                str(i),
                f"{ent['total_mentions']:,}",
                f"[{style}]{etype}[/{style}]" if style else etype,
                ent["entity_text"],
            )
        console.print(table)
    except ImportError:
        print(f"\nTop entities ({label}):\n")
        for ent in entities:
            print(f"  {ent['total_mentions']:>4}x  [{ent['entity_type']}]  {ent['entity_text']}")


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
    levels = " \u2591\u2592\u2593\u2588"

    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        level_colors = ["dim", "blue", "cyan", "yellow", "green bold"]

        header = "       " + "   ".join(f"[bold]{d}[/]" for d in days)
        rows: list[str] = [header]
        for hour in range(24):
            row_str = f"  [dim]{hour:02d}[/]   "
            for day in range(7):
                count = grid.get((hour, day), 0)
                level = int((count / max_count) * (len(levels) - 1)) if max_count > 0 else 0
                color = level_colors[level]
                row_str += f" [{color}]{levels[level]}[/{color}]  "
            rows.append(row_str)

        body = "\n".join(rows)
        legend = (
            "[dim]' '=none  [blue]\u2591[/]=low  [cyan]\u2592[/]=mid"
            f"  [yellow]\u2593[/]=high  [green bold]\u2588[/]=peak (max={max_count})[/]"
        )
        console.print(
            Panel(
                f"{body}\n\n  {legend}",
                title="[bold]Activity Heatmap (hour x day-of-week)[/]",
                border_style="blue",
            )
        )
    except ImportError:
        print("\nActivity heatmap (hour \u00d7 day-of-week):\n")
        print(f"      {'   '.join(days)}")
        for hour in range(24):
            row_str = f"  {hour:02d}  "
            for day in range(7):
                count = grid.get((hour, day), 0)
                level = int((count / max_count) * (len(levels) - 1)) if max_count > 0 else 0
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

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="[bold]Average Response Times[/]", border_style="dim")
        table.add_column("#", style="dim", width=3, justify="right")
        table.add_column("Avg Time", width=10, justify="right")
        table.add_column("Replies", width=8, justify="right", style="cyan")
        table.add_column("Replier", min_width=25)

        for i, row in enumerate(data, 1):
            hours = row["avg_response_hours"]
            if hours < 1:
                time_str = f"{hours * 60:.0f}m"
                time_style = "green bold"
            elif hours < 24:
                time_str = f"{hours:.1f}h"
                time_style = "yellow"
            else:
                time_str = f"{hours / 24:.1f}d"
                time_style = "red"
            table.add_row(
                str(i),
                f"[{time_style}]{time_str}[/{time_style}]",
                f"{row['response_count']:,}",
                row["replier"],
            )
        console.print(table)
    except ImportError:
        print("\nAverage response times:\n")
        for row in data:
            print(f"  {row['avg_response_hours']:>6.1f}h avg  ({row['response_count']:>3} replies)  {row['replier']}")


def _run_suggest() -> None:
    db = _get_email_db()
    from .query_suggestions import QuerySuggester

    suggester = QuerySuggester(db)
    suggestions = suggester.suggest_flat(limit=15)
    if not suggestions:
        print("No suggestions available. Is the SQLite database populated?")
        return

    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        lines = [f"  [cyan]\u2022[/] {s}" for s in suggestions]
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold]Query Suggestions[/]",
                border_style="blue",
            )
        )
    except ImportError:
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
    page = db.list_emails_paginated(offset=offset, limit=limit, folder=folder, sender=sender)
    total = page["total"]
    emails = page["emails"]
    page_num = (offset // limit) + 1
    total_pages = (total + limit - 1) // limit if total > 0 else 0

    if not emails:
        print("No emails found.")
        return

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(
            title=f"[bold]Emails: page {page_num}/{total_pages} ({total:,} total)[/]",
            border_style="dim",
            show_lines=True,
        )
        table.add_column("#", style="dim", width=5, justify="right")
        table.add_column("Date", width=10)
        table.add_column("Sender", width=28)
        table.add_column("Subject", min_width=30)
        table.add_column("UID", width=14, style="dim")

        for i, email in enumerate(emails, start=offset + 1):
            subj = sanitize_untrusted_text(str(email.get("subject", "(no subject)")))
            sender_val = sanitize_untrusted_text(str(email.get("sender_email", "?")))
            date_val = str(email.get("date", "?"))[:10]
            uid = email.get("uid", "?")[:12]
            table.add_row(str(i), date_val, sender_val, subj, uid)

        console.print(table)
        console.print(f"  [dim]Showing {offset + 1}\u2013{offset + len(emails)} of {total:,}[/]")
        if offset + limit < total:
            console.print(f"  [dim]Next page: browse --page {page_num + 1} --page-size {limit}[/]")
    except ImportError:
        print(f"\nBrowsing emails: page {page_num}/{total_pages} ({total} total)\n")
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
        _print_rich_or_plain(
            rich_fn=lambda c: (
                c.print("[yellow]No evidence items found.[/]"),
                c.print("[dim]Use the evidence_add MCP tool from Claude Code to start collecting evidence.[/]"),
            ),
            plain_fn=lambda: print("No evidence items found."),
        )
        return

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()

        # Summary header
        verified_count = sum(1 for i in items if i.get("verified"))
        unverified_count = len(items) - verified_count
        console.print(
            Panel(
                f"  [bold]{total}[/] total items  |  "
                f"[green bold]{verified_count}[/] verified  |  "
                f"[yellow]{unverified_count}[/] unverified",
                title="[bold]Evidence Collection[/]",
                border_style="blue",
            )
        )

        table = Table(
            border_style="dim",
            show_lines=True,
        )
        table.add_column("ID", style="dim", width=5, justify="right")
        table.add_column("Date", width=10)
        table.add_column("Status", width=8, justify="center")
        table.add_column("Relevance", width=10, justify="center")
        table.add_column("Category", width=16)
        table.add_column("Sender", width=22)
        table.add_column("Subject", width=25)
        table.add_column("Quote Preview", min_width=30)

        _CATEGORY_STYLES = {
            "bossing": "bold red",
            "harassment": "bold red",
            "discrimination": "bold red",
            "retaliation": "bold magenta",
            "hostile_environment": "bold magenta",
            "gaslighting": "bold magenta",
            "micromanagement": "yellow",
            "exclusion": "yellow",
            "workload": "yellow",
            "general": "dim",
        }

        for item in items:
            verified = "[green bold]VERIFIED[/]" if item.get("verified") else "[dim]PENDING[/]"
            rel = item.get("relevance", 0)
            stars = "[yellow]" + "\u2605" * rel + "\u2606" * (5 - rel) + "[/]"
            date_val = str(item.get("date", ""))[:10]
            cat = item.get("category", "")
            cat_style = _CATEGORY_STYLES.get(cat, "")
            cat_display = f"[{cat_style}]{cat}[/{cat_style}]" if cat_style else cat
            sender = sanitize_untrusted_text(str(item.get("sender_name") or item.get("sender_email", "?")))
            subject = sanitize_untrusted_text(str(item.get("subject", ""))[:25])
            quote_preview = item.get("key_quote", "")[:80]
            if len(item.get("key_quote", "")) > 80:
                quote_preview += "..."

            table.add_row(
                str(item["id"]),
                date_val,
                verified,
                stars,
                cat_display,
                sender,
                subject,
                f'[dim italic]"{sanitize_untrusted_text(quote_preview)}"[/]',
            )
        console.print(table)
    except ImportError:
        print(f"\nEvidence items ({total} total):\n")
        for item in items:
            verified = "VERIFIED" if item.get("verified") else "PENDING"
            stars = "*" * item.get("relevance", 0)
            date_val = str(item.get("date", ""))[:10]
            cat = item.get("category", "")
            sender = item.get("sender_name") or item.get("sender_email", "?")
            quote_preview = item.get("key_quote", "")[:60]
            if len(item.get("key_quote", "")) > 60:
                quote_preview += "..."
            print(f"  [{item['id']:>4}] {date_val}  [{verified:<8}] {stars:<5}  {cat:<20}  {sender}")
            print(f'         "{quote_preview}"')


def _run_evidence_export(
    output_path: str,
    fmt: str,
    category: str | None,
    min_relevance: int | None,
) -> None:
    db = _get_email_db()
    from .evidence_exporter import EvidenceExporter

    exporter = EvidenceExporter(db)
    result = exporter.export_file(
        output_path=output_path,
        fmt=fmt,
        min_relevance=min_relevance,
        category=category,
    )
    print(f"Evidence report exported: {result['output_path']} ({result['item_count']} items, {result['format']})")
    if "note" in result:
        print(f"  Note: {result['note']}")


def _run_evidence_stats() -> None:
    db = _get_email_db()
    stats = db.evidence_stats()

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()
        total = stats.get("total", 0)
        verified = stats.get("verified", 0)
        unverified = stats.get("unverified", 0)
        verified_pct = f"{verified / total:.0%}" if total > 0 else "N/A"

        summary = (
            f"  [bold]{total}[/] total items  |  "
            f"[green bold]{verified}[/] verified ({verified_pct})  |  "
            f"[yellow]{unverified}[/] unverified"
        )
        console.print(Panel(summary, title="[bold]Evidence Statistics[/]", border_style="blue"))

        # Relevance breakdown
        relevance_counts = stats.get("by_relevance", {})
        if relevance_counts:
            rel_table = Table(title="[bold]By Relevance Level[/]", border_style="dim")
            rel_table.add_column("Level", width=20, justify="center")
            rel_table.add_column("Count", justify="right", style="cyan bold")
            _REL_LABELS = {
                5: "[green bold]\u2605\u2605\u2605\u2605\u2605[/] Direct proof",
                4: "[green]\u2605\u2605\u2605\u2605\u2606[/] Strong evidence",
                3: "[yellow]\u2605\u2605\u2605\u2606\u2606[/] Supporting",
                2: "[yellow dim]\u2605\u2605\u2606\u2606\u2606[/] Background",
                1: "[dim]\u2605\u2606\u2606\u2606\u2606[/] Tangential",
            }
            for level in (5, 4, 3, 2, 1):
                count = relevance_counts.get(str(level), relevance_counts.get(level, 0))
                if count:
                    label = _REL_LABELS.get(level, str(level))
                    rel_table.add_row(label, str(count))
            console.print(rel_table)

        # Category breakdown if available
        categories = stats.get("categories", [])
        if categories:
            cat_table = Table(title="[bold]By Category[/]", border_style="dim")
            cat_table.add_column("Category", min_width=20)
            cat_table.add_column("Count", justify="right", style="cyan bold")
            cat_table.add_column("", width=25)
            if isinstance(categories, dict):
                cat_pairs = sorted(categories.items(), key=lambda x: x[1], reverse=True)
            else:
                cat_pairs = [(c.get("category", "?"), c.get("count", 0)) for c in categories]
            max_cat_count = max((c for _, c in cat_pairs), default=1)
            for cat_name, cat_count in cat_pairs:
                bar_len = int((cat_count / max_cat_count) * 20) if max_cat_count else 0
                bar = "[cyan]" + "\u2588" * bar_len + "[/]"
                cat_table.add_row(str(cat_name), str(cat_count), bar)
            console.print(cat_table)
    except ImportError:
        print(json.dumps(stats, indent=2))


def _run_evidence_verify() -> None:
    db = _get_email_db()
    result = db.verify_evidence_quotes()

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()
        verified = result.get("verified", 0)
        failed = result.get("failed", 0)
        total = verified + failed

        status_style = "green" if failed == 0 else "yellow"
        console.print(
            Panel(
                f"  [bold]{total}[/] quotes checked  |  "
                f"[green bold]{verified}[/] verified  |  "
                f"[{'red bold' if failed else 'dim'}]{failed}[/] failed",
                title=(
                    f"[bold]Quote Verification [{status_style}]"
                    f"{'PASSED' if failed == 0 else 'ISSUES FOUND'}[/{status_style}][/]"
                ),
                border_style=status_style,
            )
        )

        failures = result.get("failures", [])
        if failures:
            table = Table(
                title="[bold red]Failed Verifications[/]",
                border_style="red",
                show_lines=True,
            )
            table.add_column("Evidence ID", width=12, justify="right")
            table.add_column("Email UID", width=14, style="dim")
            table.add_column("Quote Preview", min_width=40)

            for f in failures:
                table.add_row(
                    str(f.get("evidence_id", "?")),
                    str(f.get("email_uid", ""))[:12],
                    f'[italic]"{sanitize_untrusted_text(f.get("key_quote_preview", ""))}"[/]',
                )
            console.print(table)
            console.print(
                "[dim]  Failed quotes may indicate modified source emails or extraction errors.\n"
                "  Use evidence_update to correct quotes against the current email body.[/]"
            )
    except ImportError:
        print(f"\nVerification complete: {result['verified']} verified, {result['failed']} failed")
        if result.get("failures"):
            print("\nFailed quotes:")
            for f in result["failures"]:
                print(f'  ID {f["evidence_id"]}: "{f["key_quote_preview"]}" (email: {f["email_uid"][:12]})')


def _run_dossier(
    output_path: str,
    fmt: str,
    category: str | None,
    min_relevance: int | None,
) -> None:
    db = _get_email_db()
    from .dossier_generator import DossierGenerator

    gen = DossierGenerator(db)
    result = gen.generate_file(
        output_path=output_path,
        fmt=fmt,
        category=category,
        min_relevance=min_relevance,
    )
    print(f"Dossier generated: {result['output_path']} ({result['evidence_count']} evidence items, {result['format']})")
    print(f"  SHA-256: {result['dossier_hash']}")


def _run_custody_chain() -> None:
    db = _get_email_db()
    events = db.get_custody_chain(limit=100)
    if not events:
        _print_rich_or_plain(
            rich_fn=lambda c: c.print("[yellow]No custody events recorded.[/]"),
            plain_fn=lambda: print("No custody events recorded."),
        )
        return

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()

        # Professional audit trail header
        console.print(
            Panel(
                f"  [bold]{len(events)}[/] custody events recorded\n"
                f"  [dim]Chain-of-custody tracking provides a forensically defensible\n"
                f"  audit trail of all evidence handling operations.[/]",
                title="[bold]Chain-of-Custody Audit Trail[/]",
                border_style="blue",
            )
        )

        _ACTION_STYLES = {
            "evidence_added": "green",
            "evidence_updated": "yellow",
            "evidence_removed": "red",
            "evidence_verified": "cyan",
            "dossier_generated": "magenta",
            "ingestion_started": "blue",
            "ingestion_completed": "blue",
        }

        table = Table(
            border_style="dim",
            show_lines=True,
        )
        table.add_column("Timestamp (UTC)", width=20)
        table.add_column("Action", width=22)
        table.add_column("Actor", width=12)
        table.add_column("Target Type", width=14)
        table.add_column("Target ID", width=14)
        table.add_column("SHA-256 (prefix)", width=20, style="dim")

        for event in events:
            action = event["action"]
            action_style = _ACTION_STYLES.get(action, "")
            action_display = f"[{action_style}]{action}[/{action_style}]" if action_style else action
            target_type = event.get("target_type", "") or ""
            target_id = str(event.get("target_id", "") or "")
            target_id_short = target_id[:12] + "..." if len(target_id) > 12 else target_id
            content_hash = event.get("content_hash") or ""
            hash_display = content_hash[:16] + "..." if content_hash else "[dim]--[/]"

            table.add_row(
                event["timestamp"],
                action_display,
                event.get("actor", "system"),
                target_type,
                target_id_short,
                hash_display,
            )
        console.print(table)
    except ImportError:
        print(f"\nChain-of-custody audit trail ({len(events)} events):\n")
        for event in events:
            target = f"{event.get('target_type', '')}:{event.get('target_id', '')}" if event.get("target_type") else ""
            content_hash = event.get("content_hash") or ""
            hash_display = content_hash[:16] + "..." if content_hash else "--"
            print(f"  {event['timestamp']}  {event['action']:<22}  {event.get('actor', 'system'):<10}  {target}")
            print(f"    SHA-256: {hash_display}")


def _run_provenance(email_uid: str) -> None:
    db = _get_email_db()
    result = db.email_provenance(email_uid)

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()

        email = result.get("email", {})
        source = result.get("source", {})
        custody = result.get("custody_events", [])

        # Email identity
        console.print(
            Panel(
                f"  [bold]Subject:[/] {email.get('subject', '(unknown)')}\n"
                f"  [bold]From:[/] {email.get('sender_email', '?')}\n"
                f"  [bold]Date:[/] {str(email.get('date', '?'))[:10]}\n"
                f"  [bold]UID:[/] [dim]{email_uid}[/]",
                title="[bold]Email Provenance[/]",
                border_style="blue",
            )
        )

        # Source tracing
        if source:
            olm_hash = source.get("olm_source_hash", "")
            ingested_at = source.get("ingested_at", "")
            console.print(
                Panel(
                    f"  [bold]OLM Source Hash:[/] [dim]{olm_hash or 'N/A'}[/]\n"
                    f"  [bold]Ingested At:[/] {ingested_at or 'N/A'}",
                    title="[bold]Source Tracing[/]",
                    border_style="cyan",
                )
            )

        # Custody events for this email
        if custody:
            table = Table(
                title=f"[bold]Custody Events ({len(custody)})[/]",
                border_style="dim",
                show_lines=True,
            )
            table.add_column("Timestamp", width=20)
            table.add_column("Action", width=22)
            table.add_column("Actor", width=12)
            table.add_column("SHA-256 (prefix)", width=20, style="dim")

            for event in custody:
                content_hash = event.get("content_hash") or ""
                hash_display = content_hash[:16] + "..." if content_hash else "[dim]--[/]"
                table.add_row(
                    event.get("timestamp", ""),
                    event.get("action", ""),
                    event.get("actor", "system"),
                    hash_display,
                )
            console.print(table)
        else:
            console.print("[dim]  No custody events recorded for this email.[/]")
    except ImportError:
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
