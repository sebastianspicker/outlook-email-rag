# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to semantic versioning principles for public interfaces.

## [Unreleased]

### Added

- `src/__main__.py`: `python -m src` now starts the MCP server directly.
- `.claude/settings.json`: project-level MCP server registration — Claude Code auto-discovers tools when the project is opened.
- `pyproject.toml`: added `[project]` and `[build-system]` sections; `pip install -e .[dev]` now works.

#### Enhanced Parsing

- BCC recipient extraction from OLM email headers.
- Attachment metadata: MIME type, file size, filename.
- Email type detection via `in_reply_to` header (new/reply/forward).
- HTML-to-text conversion preserving headings, lists, tables, links, and blockquotes.
- Signature detection and separation from email body.
- Attachment text extraction (`src/attachment_extractor.py`): PDF, DOCX, XLSX, CSV, HTML, TXT.

#### SQLite + Analytics

- `src/email_db.py`: SQLite metadata store with emails, recipients, contacts, and communication_edges tables.
- `src/network_analysis.py`: Communication network analysis using NetworkX — centrality, communities, bridge nodes.
- `src/temporal_analysis.py`: Time-series analytics using pandas — volume over time, activity heatmaps, response times.
- `src/entity_extractor.py`: Regex-based entity extraction (organizations, URLs, phones, @mentions).

#### Advanced Search

- `src/bm25_index.py`: BM25 keyword index for hybrid semantic + keyword search.
- `src/reranker.py`: Cross-encoder reranking for improved result precision.
- `src/query_expander.py`: Semantic query expansion using vocabulary similarity.
- `src/query_suggestions.py`: Search suggestion generation from indexed data.

#### NLP Intelligence

- `src/nlp_entity_extractor.py`: spaCy NER for person, organization, and location extraction.
- `src/topic_modeler.py`: NMF topic modeling with TF-IDF.
- `src/keyword_extractor.py`: TF-IDF keyword extraction (global and per-sender/folder).
- `src/email_clusterer.py`: KMeans email clustering with automatic labeling.
- `src/thread_summarizer.py`: Extractive thread summarization.
- `src/thread_intelligence.py`: Action item and decision extraction from email threads.
- `src/writing_analyzer.py`: Writing style and readability metrics (Flesch Reading Ease, grade level).

#### Data Quality

- `src/dedup_detector.py`: Near-duplicate email detection using character n-gram similarity.
- `src/language_detector.py`: Language detection and distribution statistics.
- `src/sentiment_analyzer.py`: Rule-based sentiment analysis across emails.
- `src/auto_tagger.py`: Automatic email tagging and categorization.

#### Reporting

- `src/report_generator.py`: Self-contained HTML report generation using Jinja2 templates.
- `src/dashboard_charts.py`: Chart generation helpers for Streamlit dashboard.

#### Email Export & Browse

- `src/email_exporter.py`: Export conversation threads and single emails as styled HTML or PDF (Jinja2 template, optional weasyprint for PDF).
- `src/templates/thread_export.html`: Mail-client-style HTML template with headers, body, attachment listings, and print-friendly CSS.
- SQLite schema v3: `body_text` and `body_html` columns now persist full email bodies during ingestion.
- `get_email_full()`, `get_thread_emails()`, `list_emails_paginated()` methods on `EmailDatabase`.
- `reingest_bodies()` function to backfill body text for existing databases from OLM.

#### New MCP Tools (35 new, 43 total)

- **Core Search**: `email_search_by_recipient`, `email_search_thread`, `email_smart_search`, `email_find_similar`.
- **Archive Info**: `email_query_suggestions`.
- **Email Reading & Export**: `email_get_full`, `email_browse`, `email_export_thread`, `email_export_single`.
- **Ingestion**: `email_reingest_bodies`.
- **Network Analysis**: `email_top_contacts`, `email_communication_between`, `email_network_analysis`.
- **Temporal Analysis**: `email_volume_over_time`, `email_activity_pattern`, `email_response_times`.
- **Entity & NLP**: `email_search_by_entity`, `email_list_entities`, `email_entity_network`, `email_find_people`, `email_entity_timeline`.
- **Thread Intelligence**: `email_thread_summary`, `email_action_items`, `email_decisions`.
- **Topics & Clusters**: `email_topics`, `email_search_by_topic`, `email_keywords`, `email_clusters`, `email_cluster_emails`.
- **Data Quality**: `email_find_duplicates`, `email_language_stats`, `email_sentiment_overview`.
- **Reporting & Export**: `email_generate_report`, `email_export_network`, `email_writing_analysis`.

#### New CLI Flags

- `--to`, `--bcc`, `--has-attachments`, `--priority`, `--email-type` filters.
- `--rerank`, `--hybrid`, `--expand-query` search mode flags.
- `--topic`, `--cluster-id` semantic filter flags.
- `--suggest` for search suggestions.
- `--top-contacts EMAIL` for communication partners.
- `--volume {day,week,month}` for email volume trends.
- `--entities` for entity listing.
- `--heatmap` for activity pattern.
- `--response-times` for response time stats.
- `--generate-report` for HTML report generation.
- `--export-network` for GraphML network export.
- `--browse` for paginated email browsing.
- `--page`, `--page-size` for browse pagination control.
- `--export-thread CONV_ID` for thread export.
- `--export-email UID` for single email export.
- `--export-format {html,pdf}` for export format selection.
- `--output PATH` (`-o`) for export output path.

#### Streamlit UI Enhancements

- To filter field in search form.
- Has-attachments checkbox filter.
- Email type and attachment badges in results.
- To field display in result cards.
- Thread view button for conversation exploration.
- CSV export for search results.
- Folder sidebar with counts.
- Date picker widgets (replaced manual text inputs).
- Pagination (20 results per page).
- CC filter field in search form.
- Improved empty-state message with ingestion instructions.

#### Other

- New MCP tool `email_list_folders`: lists archive folders with email counts.
- New MCP tool `email_ingest`: triggers `.olm` ingestion directly from Claude Code.
- `--cc` filter flag for CLI search.
- `--version` flag for CLI.
- `list_folders()` method on `EmailRetriever`.
- CC recipient filter in `search_filtered()` and `email_search_structured`.
- Shared `positive_int()` validator in `src/validation.py`.
- To, BCC, has_attachments, priority filters in `search_filtered()`.

#### Dependencies Added

- `networkx>=3.2` — communication network analysis.
- `pandas>=2.1.0` — temporal analytics.
- `rank_bm25>=0.2.2` — BM25 keyword search.
- `jinja2>=3.0.0` — HTML report generation.
- `spacy>=3.7.0` — NLP entity extraction.
- `textstat>=0.7.0` — writing readability metrics.
- `streamlit>=1.40.0` — web UI.

### Changed

- Removed `anthropic` SDK dependency — MCP server is now the sole Claude integration point.
- Removed `ask_claude()` synthesis from CLI; results are always shown as formatted retrieval output.
- Removed `--no-claude` and `--raw` CLI flags (replaced by `--format {text,json}`).
- Removed `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` from configuration.
- Removed over-engineered governance docs: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `docs/RELEASE_CHECKLIST.md`, `docs/RELEASE_FILE_MANIFEST.md`, `docs/TEST_ACCEPTANCE_MATRIX.md`, GitHub issue/PR templates, and release workflow.
- Removed `src/converters.py`; `to_builtin_list()` moved to `src/storage.py`.
- Removed backward-compat wrapper methods `search_by_sender()` and `search_by_date()` from `EmailRetriever`; callers now use `search_filtered()` directly.
- `email_search_by_sender` and `email_search_by_date` MCP tools now call `search_filtered()` directly.
- Simplified `_serialize_results` helper in MCP server to direct `retriever.serialize_results()` call.
- Removed trivial wrapper functions `_sanitize_terminal_text()` (CLI) and `_sanitize_untrusted_text()` (MCP server).
- Ingest progress log interval reduced from 500 to 100 emails.
- README rewritten to reflect 43 MCP tools, 673 tests, full architecture.
- `docs/API_COMPATIBILITY.md` expanded to cover all 43 tools and all CLI flags.

### Breaking

- `--no-claude` and `--raw` CLI flags removed. Use `--format {text,json}` instead.
- `EmailRetriever.search_by_sender()` and `search_by_date()` methods removed. Use `search_filtered()`.
- `src.converters` module removed. Import `to_builtin_list` from `src.storage`.

## [0.1.0] - 2026-03-02

### Added

- Initial public release for local Outlook email RAG.
- Interfaces:
  - CLI for search and operational commands.
  - MCP server tools for agent integration.
  - Optional local Streamlit UI.
- Safety and quality gates:
  - Linting, typing, tests, static security scan, dependency audit.

### Security

- Input validation and output sanitization for untrusted email content pathways.
- Safe XML parsing constraints for OLM ingestion.

### Policy

- CLI and MCP interfaces are considered stable for this release series. Breaking changes require an explicit changelog `Breaking` entry and version bump.
- See [API compatibility policy](docs/API_COMPATIBILITY.md).
