"""Tests for CLI subcommand parsing and backward compatibility."""

from __future__ import annotations

import warnings

import pytest

from src.cli import parse_args

# ── Search subcommand ────────────────────────────────────────────


class TestSearchSubcommand:
    def test_search_with_positional_query(self):
        args = parse_args(["search", "budget review"])
        assert args.subcommand == "search"
        assert args.query == "budget review"

    def test_search_with_query_flag(self):
        args = parse_args(["search", "--query", "budget review"])
        assert args.subcommand == "search"
        assert args.query == "budget review"

    def test_search_with_filters(self):
        args = parse_args([
            "search", "--query", "budget", "--sender", "john",
            "--date-from", "2024-01-01", "--rerank", "--top-k", "5",
        ])
        assert args.subcommand == "search"
        assert args.query == "budget"
        assert args.sender == "john"
        assert args.date_from == "2024-01-01"
        assert args.rerank is True
        assert args.top_k == 5

    def test_search_with_format_json(self):
        args = parse_args(["search", "--query", "test", "--format", "json"])
        assert args.subcommand == "search"
        assert args.format == "json"

    def test_search_rejects_both_positional_and_flag_query(self):
        with pytest.raises(SystemExit):
            parse_args(["search", "positional query", "--query", "flag query"])

    def test_search_requires_query(self):
        with pytest.raises(SystemExit):
            parse_args(["search"])

    def test_search_with_all_metadata_filters(self):
        args = parse_args([
            "search", "--query", "test",
            "--subject", "approval",
            "--folder", "inbox",
            "--cc", "team",
            "--to", "boss",
            "--bcc", "hr",
            "--has-attachments",
            "--priority", "3",
            "--email-type", "reply",
            "--min-score", "0.7",
            "--hybrid",
            "--topic", "2",
            "--cluster-id", "4",
            "--expand-query",
        ])
        assert args.subcommand == "search"
        assert args.subject == "approval"
        assert args.folder == "inbox"
        assert args.cc == "team"
        assert args.to == "boss"
        assert args.bcc == "hr"
        assert args.has_attachments is True
        assert args.priority == 3
        assert args.email_type == "reply"
        assert args.min_score == 0.7
        assert args.hybrid is True
        assert args.topic == 2
        assert args.cluster_id == 4
        assert args.expand_query is True


# ── Browse subcommand ────────────────────────────────────────────


class TestBrowseSubcommand:
    def test_browse_defaults(self):
        args = parse_args(["browse"])
        assert args.subcommand == "browse"
        assert args.page == 1
        assert args.page_size == 20

    def test_browse_with_page_and_size(self):
        args = parse_args(["browse", "--page", "3", "--page-size", "30"])
        assert args.page == 3
        assert args.page_size == 30

    def test_browse_with_filters(self):
        args = parse_args(["browse", "--folder", "inbox", "--sender", "alice"])
        assert args.folder == "inbox"
        assert args.sender == "alice"


# ── Export subcommand ────────────────────────────────────────────


class TestExportSubcommand:
    def test_export_thread(self):
        args = parse_args(["export", "thread", "conv-123"])
        assert args.subcommand == "export"
        assert args.export_action == "thread"
        assert args.conversation_id == "conv-123"

    def test_export_thread_with_format_and_output(self):
        args = parse_args([
            "export", "thread", "conv-123", "--format", "pdf", "-o", "out.pdf",
        ])
        assert args.format == "pdf"
        assert args.output == "out.pdf"

    def test_export_email(self):
        args = parse_args(["export", "email", "uid-abc"])
        assert args.subcommand == "export"
        assert args.export_action == "email"
        assert args.uid == "uid-abc"

    def test_export_report(self):
        args = parse_args(["export", "report"])
        assert args.subcommand == "export"
        assert args.export_action == "report"
        assert args.output == "report.html"

    def test_export_report_custom_output(self):
        args = parse_args(["export", "report", "--output", "custom.html"])
        assert args.output == "custom.html"

    def test_export_network(self):
        args = parse_args(["export", "network"])
        assert args.subcommand == "export"
        assert args.export_action == "network"
        assert args.output == "network.graphml"


# ── Evidence subcommand ──────────────────────────────────────────


class TestEvidenceSubcommand:
    def test_evidence_list(self):
        args = parse_args(["evidence", "list"])
        assert args.subcommand == "evidence"
        assert args.evidence_action == "list"

    def test_evidence_list_with_filters(self):
        args = parse_args([
            "evidence", "list", "--category", "discrimination",
            "--min-relevance", "3",
        ])
        assert args.category == "discrimination"
        assert args.min_relevance == 3

    def test_evidence_export(self):
        args = parse_args(["evidence", "export", "report.html"])
        assert args.evidence_action == "export"
        assert args.output_path == "report.html"

    def test_evidence_export_csv(self):
        args = parse_args([
            "evidence", "export", "data.csv", "--format", "csv",
        ])
        assert args.format == "csv"

    def test_evidence_stats(self):
        args = parse_args(["evidence", "stats"])
        assert args.evidence_action == "stats"

    def test_evidence_verify(self):
        args = parse_args(["evidence", "verify"])
        assert args.evidence_action == "verify"

    def test_evidence_dossier(self):
        args = parse_args(["evidence", "dossier", "output.html"])
        assert args.evidence_action == "dossier"
        assert args.output_path == "output.html"

    def test_evidence_dossier_pdf(self):
        args = parse_args(["evidence", "dossier", "out.pdf", "--format", "pdf"])
        assert args.format == "pdf"

    def test_evidence_custody(self):
        args = parse_args(["evidence", "custody"])
        assert args.evidence_action == "custody"

    def test_evidence_provenance(self):
        args = parse_args(["evidence", "provenance", "uid-xyz"])
        assert args.evidence_action == "provenance"
        assert args.uid == "uid-xyz"


# ── Analytics subcommand ─────────────────────────────────────────


class TestAnalyticsSubcommand:
    def test_analytics_stats(self):
        args = parse_args(["analytics", "stats"])
        assert args.subcommand == "analytics"
        assert args.analytics_action == "stats"

    def test_analytics_senders_default(self):
        args = parse_args(["analytics", "senders"])
        assert args.analytics_action == "senders"
        assert args.limit == 30

    def test_analytics_senders_custom(self):
        args = parse_args(["analytics", "senders", "50"])
        assert args.limit == 50

    def test_analytics_suggest(self):
        args = parse_args(["analytics", "suggest"])
        assert args.analytics_action == "suggest"

    def test_analytics_contacts(self):
        args = parse_args(["analytics", "contacts", "alice@example.com"])
        assert args.analytics_action == "contacts"
        assert args.email_address == "alice@example.com"

    def test_analytics_volume_default(self):
        args = parse_args(["analytics", "volume"])
        assert args.analytics_action == "volume"
        assert args.period == "month"

    def test_analytics_volume_week(self):
        args = parse_args(["analytics", "volume", "week"])
        assert args.period == "week"

    def test_analytics_entities(self):
        args = parse_args(["analytics", "entities"])
        assert args.analytics_action == "entities"
        assert args.entity_type is None

    def test_analytics_entities_with_type(self):
        args = parse_args(["analytics", "entities", "--type", "organization"])
        assert args.entity_type == "organization"

    def test_analytics_heatmap(self):
        args = parse_args(["analytics", "heatmap"])
        assert args.analytics_action == "heatmap"

    def test_analytics_response_times(self):
        args = parse_args(["analytics", "response-times"])
        assert args.analytics_action == "response-times"


# ── Training subcommand ──────────────────────────────────────────


class TestTrainingSubcommand:
    def test_training_generate_data(self):
        args = parse_args(["training", "generate-data", "output.jsonl"])
        assert args.subcommand == "training"
        assert args.training_action == "generate-data"
        assert args.output_path == "output.jsonl"

    def test_training_fine_tune(self):
        args = parse_args(["training", "fine-tune", "data.jsonl"])
        assert args.training_action == "fine-tune"
        assert args.data_path == "data.jsonl"
        assert args.output_dir == "models/fine-tuned"
        assert args.epochs == 3

    def test_training_fine_tune_custom(self):
        args = parse_args([
            "training", "fine-tune", "data.jsonl",
            "--output-dir", "models/custom", "--epochs", "5",
        ])
        assert args.output_dir == "models/custom"
        assert args.epochs == 5


# ── Admin subcommand ─────────────────────────────────────────────


class TestAdminSubcommand:
    def test_admin_reset_index_without_yes(self):
        args = parse_args(["admin", "reset-index"])
        assert args.subcommand == "admin"
        assert args.admin_action == "reset-index"
        assert args.yes is False

    def test_admin_reset_index_with_yes(self):
        args = parse_args(["admin", "reset-index", "--yes"])
        assert args.yes is True


# ── Shared flags ─────────────────────────────────────────────────


class TestSharedFlags:
    def test_chromadb_path_on_search(self):
        args = parse_args(["search", "--query", "test", "--chromadb-path", "/tmp/db"])
        assert args.chromadb_path == "/tmp/db"
        assert args.subcommand == "search"

    def test_log_level_on_browse(self):
        args = parse_args(["browse", "--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"
        assert args.subcommand == "browse"

    def test_chromadb_path_on_analytics(self):
        args = parse_args(["analytics", "--chromadb-path", "/tmp/db", "stats"])
        assert args.chromadb_path == "/tmp/db"

    def test_chromadb_path_on_legacy(self):
        """Legacy mode supports --chromadb-path."""
        args = parse_args(["--chromadb-path", "/tmp/db", "--stats"])
        assert args.chromadb_path == "/tmp/db"


# ── Legacy backward compatibility ────────────────────────────────


class TestLegacyBackwardCompat:
    def test_legacy_query_flag_still_works(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            args = parse_args(["--query", "budget"])
            assert args.subcommand == "search"
            assert args.query == "budget"
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_legacy_stats_flag_still_works(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            args = parse_args(["--stats"])
            assert args.subcommand == "analytics"
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_legacy_browse_flag_still_works(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            args = parse_args(["--browse"])
            assert args.subcommand == "browse"
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_legacy_export_thread_still_works(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            args = parse_args(["--export-thread", "conv-123"])
            assert args.subcommand == "export"
            assert args.export_thread == "conv-123"
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_legacy_evidence_list_still_works(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            args = parse_args(["--evidence-list"])
            assert args.subcommand == "evidence"
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_legacy_reset_index_still_works(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            args = parse_args(["--reset-index"])
            assert args.subcommand == "admin"
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_legacy_fine_tune_still_works(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            args = parse_args(["--generate-training-data", "out.jsonl"])
            assert args.subcommand == "training"
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_no_subcommand_no_flags_returns_none(self):
        args = parse_args([])
        assert args.subcommand is None

    def test_legacy_validation_still_applies(self):
        """Mutually exclusive operations still rejected in legacy mode."""
        with pytest.raises(SystemExit):
            parse_args(["--stats", "--suggest"])


# ── Subcommand detection ─────────────────────────────────────────


class TestSubcommandDetection:
    def test_has_subcommand_with_search(self):
        from src.cli import _has_subcommand

        assert _has_subcommand(["search", "--query", "test"]) is True

    def test_has_subcommand_with_legacy_flags(self):
        from src.cli import _has_subcommand

        assert _has_subcommand(["--query", "test"]) is False

    def test_has_subcommand_empty(self):
        from src.cli import _has_subcommand

        assert _has_subcommand([]) is False

    def test_has_subcommand_with_global_flags_before(self):
        from src.cli import _has_subcommand

        # --chromadb-path is a flag, "search" should still be found
        # but --chromadb-path has an argument, so "search" is position 3
        # The function skips flags starting with "-"
        assert _has_subcommand(["--log-level", "DEBUG", "browse"]) is False
        # In practice, the first non-flag is "DEBUG" which is not a subcommand

    def test_has_subcommand_all_valid_names(self):
        from src.cli import _has_subcommand

        for name in ["search", "browse", "export", "evidence",
                      "analytics", "training", "admin"]:
            assert _has_subcommand([name]) is True, f"{name} not detected"

    def test_has_subcommand_ignores_flag_values(self):
        """Flag values like --db-path /tmp/analytics should not be detected as subcommands."""
        from src.cli import _has_subcommand

        assert _has_subcommand(["--db-path", "analytics"]) is False
        assert _has_subcommand(["--chromadb-path", "/tmp/admin"]) is False
        assert _has_subcommand(["--log-level", "search"]) is False


# ── Legacy dispatch sets action attributes ────────────────────────


class TestLegacyDispatchActions:
    """Verify _infer_subcommand sets *_action attributes for legacy flags."""

    def test_stats_sets_analytics_action(self):
        args = parse_args(["--stats"])
        assert args.subcommand == "analytics"
        assert args.analytics_action == "stats"

    def test_list_senders_sets_analytics_action(self):
        args = parse_args(["--list-senders", "20"])
        assert args.subcommand == "analytics"
        assert args.analytics_action == "senders"

    def test_suggest_sets_analytics_action(self):
        args = parse_args(["--suggest"])
        assert args.subcommand == "analytics"
        assert args.analytics_action == "suggest"

    def test_volume_sets_analytics_action(self):
        args = parse_args(["--volume", "month"])
        assert args.subcommand == "analytics"
        assert args.analytics_action == "volume"

    def test_heatmap_sets_analytics_action(self):
        args = parse_args(["--heatmap"])
        assert args.subcommand == "analytics"
        assert args.analytics_action == "heatmap"

    def test_response_times_sets_analytics_action(self):
        args = parse_args(["--response-times"])
        assert args.subcommand == "analytics"
        assert args.analytics_action == "response-times"

    def test_evidence_list_sets_evidence_action(self):
        args = parse_args(["--evidence-list"])
        assert args.subcommand == "evidence"
        assert args.evidence_action == "list"

    def test_evidence_stats_sets_evidence_action(self):
        args = parse_args(["--evidence-stats"])
        assert args.subcommand == "evidence"
        assert args.evidence_action == "stats"

    def test_evidence_verify_sets_evidence_action(self):
        args = parse_args(["--evidence-verify"])
        assert args.subcommand == "evidence"
        assert args.evidence_action == "verify"

    def test_reset_index_sets_admin_action(self):
        args = parse_args(["--reset-index"])
        assert args.subcommand == "admin"
        assert args.admin_action == "reset-index"

    def test_generate_training_data_sets_training_action(self):
        args = parse_args(["--generate-training-data", "out.jsonl"])
        assert args.subcommand == "training"
        assert args.training_action == "generate-data"
