# API Compatibility Policy

This document defines the public interface compatibility contract for `outlook-email-rag`.

## Scope

Stable public interfaces for the `0.1.x` release series:

1. CLI arguments and behavior in `python -m src.cli`.
2. MCP tool names and parameter schemas exposed by `python -m src.mcp_server`.

## CLI Compatibility Contract

The following CLI capabilities are stable for `0.1.x`:

1. Query path:
   1. `--query` / `-q`
   2. `--top-k`
   3. `--sender`
   4. `--subject`
   5. `--folder`
   6. `--cc`
   7. `--to`
   8. `--bcc`
   9. `--has-attachments`
   10. `--priority`
   11. `--email-type`
   12. `--date-from`
   13. `--date-to`
   14. `--min-score`
   15. `--rerank`
   16. `--hybrid`
   17. `--topic`
   18. `--cluster-id`
   19. `--expand-query`
   20. `--json` and `--format {text,json}`
   21. `--version`
2. Operational path:
   1. `--stats`
   2. `--list-senders N`
   3. `--reset-index --yes`
   4. `--suggest`
   5. `--top-contacts EMAIL`
   6. `--volume {day,week,month}`
   7. `--entities`
   8. `--heatmap`
   9. `--response-times`
   10. `--generate-report`
   11. `--export-network`
3. Export & browse path:
   1. `--browse`
   2. `--page`, `--page-size`
   3. `--export-thread CONVERSATION_ID`
   4. `--export-email UID`
   5. `--export-format {html,pdf}`
   6. `--output PATH` / `-o`
4. Evidence path:
   1. `--evidence-list`
   2. `--evidence-export PATH`
   3. `--evidence-export-format {html,csv,pdf}`
   4. `--evidence-stats`
   5. `--evidence-verify`
   6. `--category`
   7. `--min-relevance`
5. Chain of custody & dossier path:
   1. `--dossier PATH`
   2. `--dossier-format {html,pdf}`
   3. `--custody-chain`
   4. `--provenance UID`
6. Fine-tuning & image embedding path:
   1. `--generate-training-data [PATH]`
   2. `--fine-tune [PATH]`
   3. `--embed-images`
7. Validation semantics:
   1. `--date-from` and `--date-to` are ISO `YYYY-MM-DD`.
   2. `--date-from` must be less than or equal to `--date-to`.
   3. `--min-score` is bounded to `[0.0, 1.0]`.
   4. `--top-k` is a positive integer and bounded.

## MCP Compatibility Contract

The following 70 tool names are stable for `0.1.x`:

### Core Search (5)

1. `email_search`
2. `email_search_structured`
3. `email_search_thread`
4. `email_smart_search`
5. `email_find_similar`

### Archive Info (4)

6. `email_list_senders`
7. `email_list_folders`
8. `email_stats`
9. `email_query_suggestions`

### Ingestion (1)

10. `email_ingest`

### Network Analysis (3)

11. `email_top_contacts`
12. `email_communication_between`
13. `email_network_analysis`

### Temporal Analysis (3)

14. `email_volume_over_time`
15. `email_activity_pattern`
16. `email_response_times`

### Entity & NLP (5)

17. `email_search_by_entity`
18. `email_list_entities`
19. `email_entity_network`
20. `email_find_people`
21. `email_entity_timeline`

### Thread Intelligence (4)

22. `email_thread_summary`
23. `email_action_items`
24. `email_decisions`
25. `email_search_by_thread_topic`

### Topics & Clusters (5)

26. `email_topics`
27. `email_search_by_topic`
28. `email_keywords`
29. `email_clusters`
30. `email_cluster_emails`

### Data Quality (3)

31. `email_find_duplicates`
32. `email_language_stats`
33. `email_sentiment_overview`

### Reporting & Export (3)

34. `email_generate_report`
35. `email_export_network`
36. `email_writing_analysis`

### Email Reading & Export (3)

37. `email_get_full`
38. `email_browse`
39. `email_export`

### Categories & Calendar (2)

40. `email_list_categories`
41. `email_browse_calendar`

### Attachments (3)

42. `email_search_by_attachment`
43. `email_list_attachments`
44. `email_attachment_stats`

### Diagnostics (5)

45. `email_diagnostics`
46. `email_reembed`
47. `email_reingest_bodies`
48. `email_reingest_metadata`
49. `email_reingest_analytics`

### Evidence Management (12)

50. `evidence_add`
51. `evidence_list`
52. `evidence_get`
53. `evidence_update`
54. `evidence_remove`
55. `evidence_verify`
56. `evidence_export`
57. `evidence_stats`
58. `evidence_add_batch`
59. `evidence_search`
60. `evidence_timeline`
61. `evidence_categories`

### Chain of Custody (3)

62. `custody_chain`
63. `email_provenance`
64. `evidence_provenance`

### Relationship Analysis (4)

65. `relationship_paths`
66. `shared_recipients`
67. `coordinated_timing`
68. `relationship_summary`

### Proof Dossier (2)

69. `dossier_generate`
70. `dossier_preview`

## Stable MCP Input Schema Summary

### Core Search

1. `email_search`
   1. `query: str` (required, 1-500 chars)
   2. `top_k: int` (optional, bounded 1-30)
2. `email_search_structured`
   1. `query: str` (required)
   2. `top_k: int` (optional, bounded 1-30)
   3. `sender: str | null` (optional)
   4. `subject: str | null` (optional)
   5. `folder: str | null` (optional)
   6. `cc: str | null` (optional)
   7. `to: str | null` (optional)
   8. `bcc: str | null` (optional)
   9. `has_attachments: bool | null` (optional)
   10. `attachment_name: str | null` (optional) — partial filename match
   11. `attachment_type: str | null` (optional) — file extension filter
   12. `priority: int | null` (optional, ge=0)
   13. `date_from: str | null` (optional, ISO date)
   14. `date_to: str | null` (optional, ISO date)
   15. `min_score: float | null` (optional, bounded 0.0-1.0)
   16. `rerank: bool` (optional, default false)
   17. `hybrid: bool` (optional, default false)
   18. `topic_id: int | null` (optional, ge=0)
   19. `cluster_id: int | null` (optional, ge=0)
   20. `expand_query: bool` (optional, default false)
   21. `email_type: str | null` (optional, one of reply/forward/original)
3. `email_search_thread`
   1. `conversation_id: str` (required)
   2. `top_k: int` (optional, bounded 1-100)
4. `email_smart_search`
   1. `query: str` (required, 1-500 chars)
   2. `top_k: int` (optional, bounded 1-30)
5. `email_find_similar`
   1. `text: str` (required)
   2. `top_k: int` (optional, bounded 1-30)

### Archive Info

6. `email_list_senders`
   1. `limit: int` (optional, bounded 1-200)
7. `email_list_folders`
    1. no parameters
8. `email_stats`
    1. no parameters
9. `email_query_suggestions`
    1. `query: str` (optional)
    2. `top_k: int` (optional)

### Ingestion

10. `email_ingest`
    1. `olm_path: str` (required) — absolute path to `.olm` file
    2. `max_emails: int | null` (optional, ge=1)
    3. `dry_run: bool` (optional, default false)
    4. `extract_attachments: bool` (optional, default false) — extract and index text from attachments
    5. `embed_images: bool` (optional, default false) — embed image attachments using Visualized-BGE-M3
    6. `extract_entities: bool` (optional, default false) — extract entities into SQLite

### Network Analysis

11. `email_top_contacts`
    1. `email: str` (required)
    2. `top_k: int` (optional)
12. `email_communication_between`
    1. `email_a: str` (required)
    2. `email_b: str` (required)
13. `email_network_analysis`
    1. `top_k: int` (optional)

### Temporal Analysis

14. `email_volume_over_time`
    1. `granularity: str` (optional, one of day/week/month)
15. `email_activity_pattern`
    1. no parameters
16. `email_response_times`
    1. `top_k: int` (optional)

### Entity & NLP

17. `email_search_by_entity`
    1. `entity: str` (required)
    2. `entity_type: str | null` (optional)
    3. `top_k: int` (optional)
18. `email_list_entities`
    1. `entity_type: str | null` (optional)
    2. `top_k: int` (optional)
19. `email_entity_network`
    1. `entity: str` (required)
    2. `top_k: int` (optional)
20. `email_find_people`
    1. `name: str` (required)
    2. `top_k: int` (optional)
21. `email_entity_timeline`
    1. `entity: str` (required)
    2. `granularity: str` (optional, one of day/week/month)

### Thread Intelligence

22. `email_thread_summary`
    1. `query: str` (required)
    2. `top_k: int` (optional)
23. `email_action_items`
    1. `query: str | null` (optional)
    2. `top_k: int` (optional)
24. `email_decisions`
    1. `query: str | null` (optional)
    2. `top_k: int` (optional)
25. `email_search_by_thread_topic`
    1. `thread_topic: str` (required) — thread topic string to search for
    2. `top_k: int` (optional)

### Topics & Clusters

26. `email_topics`
    1. no parameters
27. `email_search_by_topic`
    1. `topic_id: int` (required, ge=0)
    2. `top_k: int` (optional)
28. `email_keywords`
    1. `sender: str | null` (optional)
    2. `folder: str | null` (optional)
    3. `top_k: int` (optional)
29. `email_clusters`
    1. no parameters
30. `email_cluster_emails`
    1. `cluster_id: int` (required, ge=0)
    2. `top_k: int` (optional)

### Data Quality

31. `email_find_duplicates`
    1. `threshold: float` (optional, bounded 0.0-1.0)
    2. `top_k: int` (optional)
32. `email_language_stats`
    1. no parameters
33. `email_sentiment_overview`
    1. no parameters

### Reporting & Export

34. `email_generate_report`
    1. `output_path: str | null` (optional)
35. `email_export_network`
    1. `output_path: str | null` (optional)
36. `email_writing_analysis`
    1. `sender: str | null` (optional)
    2. `top_k: int` (optional)

### Email Reading & Export

37. `email_get_full`
    1. `uid: str` (required)
38. `email_browse`
    1. `offset: int` (optional, default 0, ge=0)
    2. `limit: int` (optional, default 20, bounded 1-50)
    3. `folder: str | null` (optional, exact match)
    4. `sender: str | null` (optional, partial match)
    5. `sort_order: str` (optional, default "desc", one of asc/desc)
    6. `include_body: bool` (optional, default true)
39. `email_export`
    1. `uid: str | null` (optional) — export a single email by UID
    2. `conversation_id: str | null` (optional) — export a thread by conversation ID
    3. `output_path: str | null` (optional)
    4. `format: str` (optional, default "html", one of html/pdf)

### Categories & Calendar

40. `email_list_categories`
    1. no parameters
41. `email_browse_calendar`
    1. `date_from: str | null` (optional, ISO date)
    2. `date_to: str | null` (optional, ISO date)
    3. `limit: int` (optional, default 20, bounded 1-50)
    4. `offset: int` (optional, default 0, ge=0)

### Attachments

42. `email_search_by_attachment`
    1. `filename: str | null` (optional) — partial filename match
    2. `extension: str | null` (optional) — file extension (e.g. "pdf", "docx")
    3. `mime_type: str | null` (optional) — MIME type filter
    4. `top_k: int` (optional)
43. `email_list_attachments`
    1. `filename: str | null` (optional) — partial filename match
    2. `extension: str | null` (optional) — file extension filter
    3. `mime_type: str | null` (optional) — MIME type filter
    4. `sender: str | null` (optional) — sender filter
    5. `limit: int` (optional, default 20, bounded 1-100)
    6. `offset: int` (optional, default 0, ge=0)
44. `email_attachment_stats`
    1. no parameters

### Diagnostics

45. `email_diagnostics`
    1. no parameters
46. `email_reembed`
    1. no parameters
47. `email_reingest_bodies`
    1. `olm_path: str` (required) — absolute path to `.olm` file
48. `email_reingest_metadata`
    1. `olm_path: str` (required) — absolute path to `.olm` file
49. `email_reingest_analytics`
    1. no parameters

### Evidence Management

50. `evidence_add`
    1. `email_uid: str` (required)
    2. `category: str` (required)
    3. `key_quote: str` (required) — must appear verbatim in email body
    4. `summary: str` (required)
    5. `relevance: int` (required, bounded 1-5)
    6. `notes: str` (optional, default "")
51. `evidence_list`
    1. `category: str | null` (optional)
    2. `min_relevance: int | null` (optional, bounded 1-5)
    3. `email_uid: str | null` (optional)
    4. `limit: int` (optional, default 100, bounded 1-500)
    5. `offset: int` (optional, default 0, ge=0)
52. `evidence_get`
    1. `evidence_id: int` (required, ge=1)
53. `evidence_update`
    1. `evidence_id: int` (required, ge=1)
    2. `category: str | null` (optional)
    3. `key_quote: str | null` (optional)
    4. `summary: str | null` (optional)
    5. `relevance: int | null` (optional, bounded 1-5)
    6. `notes: str | null` (optional)
54. `evidence_remove`
    1. `evidence_id: int` (required, ge=1)
55. `evidence_verify`
    1. no parameters
56. `evidence_export`
    1. `output_path: str` (optional, default "evidence_report.html")
    2. `format: str` (optional, default "html", one of html/csv/pdf)
    3. `min_relevance: int | null` (optional, bounded 1-5)
    4. `category: str | null` (optional)
57. `evidence_stats`
    1. `category: str | null` (optional, filter by evidence category)
    2. `min_relevance: int | null` (optional, bounded 1-5)
58. `evidence_add_batch`
    1. `items: list[EvidenceAddInput]` (required, 1-20 items)
59. `evidence_search`
    1. `query: str` (required, 1-500 chars)
    2. `category: str | null` (optional)
    3. `min_relevance: int | null` (optional, bounded 1-5)
    4. `limit: int` (optional, default 50, bounded 1-200)
60. `evidence_timeline`
    1. `category: str | null` (optional)
    2. `min_relevance: int | null` (optional, bounded 1-5)
61. `evidence_categories`
    1. no parameters

### Chain of Custody

62. `custody_chain`
    1. `target_type: str | null` (optional)
    2. `target_id: str | null` (optional)
    3. `action: str | null` (optional)
    4. `limit: int` (optional, default 50, bounded 1-500)
63. `email_provenance`
    1. `email_uid: str` (required)
64. `evidence_provenance`
    1. `evidence_id: int` (required, ge=1)

### Relationship Analysis

65. `relationship_paths`
    1. `source: str` (required)
    2. `target: str` (required)
    3. `max_hops: int` (optional, default 3, bounded 1-6)
    4. `top_k: int` (optional, default 5, bounded 1-20)
66. `shared_recipients`
    1. `email_addresses: list[str]` (required, 2-20 items)
    2. `min_shared: int` (optional, default 2, ge=2)
67. `coordinated_timing`
    1. `email_addresses: list[str]` (required, 2-20 items)
    2. `window_hours: int` (optional, default 24, bounded 1-168)
    3. `min_events: int` (optional, default 3, bounded 2-50)
68. `relationship_summary`
    1. `email_address: str` (required)
    2. `limit: int` (optional, default 20, bounded 1-50)

### Proof Dossier

69. `dossier_generate`
    1. `output_path: str` (required)
    2. `format: str` (optional, default "html", one of html/pdf)
    3. `title: str` (optional, default "Proof Dossier")
    4. `case_reference: str` (optional, default "")
    5. `custodian: str` (optional, default "")
    6. `min_relevance: int | null` (optional, bounded 1-5)
    7. `category: str | null` (optional)
    8. `include_relationships: bool` (optional, default true)
    9. `include_custody: bool` (optional, default true)
    10. `persons_of_interest: list[str] | null` (optional)
70. `dossier_preview`
    1. `min_relevance: int | null` (optional, bounded 1-5)
    2. `category: str | null` (optional)

## Breaking Change Rule

A breaking change is any modification that invalidates existing automation or user workflows relying on the stable interfaces above, including:

1. Removing or renaming stable CLI flags.
2. Changing expected semantics of stable flags.
3. Removing or renaming MCP tools.
4. Altering MCP parameter names/types/requiredness in an incompatible way.

Breaking changes require:

1. A version bump.
2. A `Breaking` entry in [CHANGELOG.md](../CHANGELOG.md).
3. Updated migration guidance in release notes.
