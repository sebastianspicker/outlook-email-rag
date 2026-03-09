"""Reporting and export MCP tools."""

from __future__ import annotations

from ..mcp_models import (
    ExportNetworkInput,
    GenerateReportInput,
    WritingAnalysisInput,
)
from .utils import json_error, json_response, run_with_db


def register(mcp, deps) -> None:
    """Register reporting and export tools."""

    @mcp.tool(
        name="email_generate_report",
        annotations=deps.idempotent_write_annotations("Generate Archive Report"),
    )
    async def email_generate_report(params: GenerateReportInput) -> str:
        """Generate a self-contained HTML report of the email archive.

        The report includes: archive overview, top senders, folder distribution,
        monthly volume, top entities, and response times.
        """
        def _work(db):
            from ..report_generator import ReportGenerator

            ReportGenerator(db).generate(title=params.title, output_path=params.output_path)
            return json_response({"status": "ok", "output_path": params.output_path})
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="email_export_network",
        annotations=deps.idempotent_write_annotations("Export Communication Network"),
    )
    async def email_export_network(params: ExportNetworkInput) -> str:
        """Export the communication network as GraphML for external visualization.

        The GraphML format is supported by Gephi, Cytoscape, and other
        network analysis tools.
        """
        def _work(db):
            from ..network_analysis import CommunicationNetwork

            return json_response(CommunicationNetwork(db).export_graphml(params.output_path))
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="email_writing_analysis",
        annotations=deps.tool_annotations("Writing Style Analysis"),
    )
    async def email_writing_analysis(params: WritingAnalysisInput) -> str:
        """Analyze writing style and readability across senders.

        Computes metrics like readability score, average sentence length,
        vocabulary richness, and formality for each sender's emails.

        If a specific sender is given, returns their detailed profile.
        If omitted, compares the top senders by volume.

        Args:
            params: sender (optional str), limit (int).

        Returns:
            JSON with writing style metrics per sender.
        """
        def _run():
            from ..writing_analyzer import WritingAnalyzer

            retriever = deps.get_retriever()
            db = deps.get_email_db()
            analyzer = WritingAnalyzer()

            def _get_sender_texts(sender_filter: str, max_texts: int = 50) -> list[str]:
                """Get email texts for a sender via semantic search."""
                try:
                    results = retriever.search_filtered(
                        query="*", top_k=max_texts, sender=sender_filter,
                    )
                    return [r.text for r in results if r.text]
                except Exception:
                    return []

            if params.sender:
                texts = _get_sender_texts(params.sender, max_texts=params.limit)
                if not texts:
                    return json_error(f"No emails found for sender: {params.sender}")
                profile = analyzer.analyze_sender_profile(texts, params.sender)
                if not profile:
                    return json_error(f"Not enough content to analyze: {params.sender}")
                return json_response(profile)

            # Compare top senders
            if not db:
                return json_error("SQLite database not available.")

            try:
                senders = db.top_senders(limit=params.limit)
            except Exception:
                return json_error("Could not fetch sender list.")

            profiles = []
            for s in senders:
                email_addr = s.get("sender_email", "")
                if not email_addr:
                    continue
                texts = _get_sender_texts(email_addr, max_texts=30)
                profile = analyzer.analyze_sender_profile(texts, email_addr)
                if profile:
                    profiles.append(profile)

            return json_response(profiles)
        return await deps.offload(_run)
