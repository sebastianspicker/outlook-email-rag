import pytest

from src.ingest import main, parse_args


def test_parse_args_rejects_non_positive_batch_size():
    with pytest.raises(SystemExit):
        parse_args(["data/file.olm", "--batch-size", "0"])


def test_parse_args_rejects_non_positive_max_emails():
    with pytest.raises(SystemExit):
        parse_args(["data/file.olm", "--max-emails", "0"])


def test_main_handles_invalid_archive_path_gracefully(tmp_path, capsys):
    invalid_archive = tmp_path / "invalid.olm"
    invalid_archive.write_text("not-a-zip", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main([str(invalid_archive), "--dry-run"])

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "Invalid OLM archive" in out


def test_main_handles_missing_archive_gracefully(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["/tmp/does-not-exist.olm", "--dry-run"])

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "OLM file not found" in out


def test_main_handles_generic_oserror_gracefully(monkeypatch, capsys):
    import src.ingest as ingest_mod

    def _raise_oserror(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(ingest_mod, "ingest", _raise_oserror)

    with pytest.raises(SystemExit) as excinfo:
        main(["data/file.olm", "--dry-run"])

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "Could not read OLM archive" in out


def test_ingest_dry_run_reports_qol_stats(monkeypatch):
    import src.ingest as ingest_mod

    class _Email:
        def __init__(self, idx):
            self.idx = idx

        def to_dict(self):
            return {"id": self.idx}

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [_Email(1), _Email(2), _Email(3)])
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email['id']}-a"}, {"chunk_id": f"{email['id']}-b"}],
    )

    stats = ingest_mod.ingest("data/mock.olm", dry_run=True, batch_size=2)

    assert stats["emails_parsed"] == 3
    assert stats["chunks_created"] == 6
    assert stats["chunks_added"] == 0
    assert stats["chunks_skipped"] == 0
    assert stats["batches_written"] == 0


def test_format_ingestion_summary_includes_qol_fields():
    from src.ingest import format_ingestion_summary

    lines = format_ingestion_summary(
        {
            "emails_parsed": 10,
            "chunks_created": 20,
            "chunks_added": 18,
            "chunks_skipped": 2,
            "batches_written": 3,
            "total_in_db": 99,
            "dry_run": False,
            "elapsed_seconds": 1.5,
        }
    )

    assert "=== Ingestion Summary ===" in lines
    assert "Emails parsed: 10" in lines
    assert "Chunks created: 20" in lines
    assert "Chunks added: 18" in lines
    assert "Chunks skipped: 2" in lines
    assert "Write batches: 3" in lines
    assert "Total in DB: 99" in lines


def test_format_ingestion_summary_for_dry_run_hides_db_totals():
    from src.ingest import format_ingestion_summary

    lines = format_ingestion_summary(
        {
            "emails_parsed": 10,
            "chunks_created": 20,
            "chunks_added": 0,
            "chunks_skipped": 0,
            "batches_written": 0,
            "total_in_db": None,
            "dry_run": True,
            "elapsed_seconds": 1.5,
        }
    )

    assert "Database write disabled (dry-run)." in lines
    assert not any(line.startswith("Chunks added:") for line in lines)
    assert not any(line.startswith("Total in DB:") for line in lines)
