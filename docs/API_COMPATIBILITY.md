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
3. Validation semantics:
   1. `--date-from` and `--date-to` are ISO `YYYY-MM-DD`.
   2. `--date-from` must be less than or equal to `--date-to`.
   3. `--min-score` is bounded to `[0.0, 1.0]`.
   4. `--top-k` is a positive integer and bounded.

## MCP Compatibility Contract

The following 38 tool names are stable for `0.1.x`:

### Core Search (8)

1. `email_search`
2. `email_search_by_sender`
3. `email_search_by_date`
4. `email_search_by_recipient`
5. `email_search_structured`
6. `email_search_thread`
7. `email_smart_search`
8. `email_find_similar`

### Archive Info (4)

9. `email_list_senders`
10. `email_list_folders`
11. `email_stats`
12. `email_query_suggestions`

### Ingestion (1)

13. `email_ingest`

### Network Analysis (3)

14. `email_top_contacts`
15. `email_communication_between`
16. `email_network_analysis`

### Temporal Analysis (3)

17. `email_volume_over_time`
18. `email_activity_pattern`
19. `email_response_times`

### Entity & NLP (5)

20. `email_search_by_entity`
21. `email_list_entities`
22. `email_entity_network`
23. `email_find_people`
24. `email_entity_timeline`

### Thread Intelligence (3)

25. `email_thread_summary`
26. `email_action_items`
27. `email_decisions`

### Topics & Clusters (5)

28. `email_topics`
29. `email_search_by_topic`
30. `email_keywords`
31. `email_clusters`
32. `email_cluster_emails`

### Data Quality (3)

33. `email_find_duplicates`
34. `email_language_stats`
35. `email_sentiment_overview`

### Reporting & Export (3)

36. `email_generate_report`
37. `email_export_network`
38. `email_writing_analysis`

## Stable MCP Input Schema Summary

### Core Search

1. `email_search`
   1. `query: str` (required, 1-500 chars)
   2. `top_k: int` (optional, bounded 1-30)
2. `email_search_by_sender`
   1. `query: str` (required)
   2. `sender: str` (required)
   3. `top_k: int` (optional, bounded 1-30)
3. `email_search_by_date`
   1. `query: str` (required)
   2. `date_from: str | null` (optional, ISO date)
   3. `date_to: str | null` (optional, ISO date)
   4. `top_k: int` (optional, bounded 1-30)
4. `email_search_by_recipient`
   1. `query: str` (required)
   2. `recipient: str` (required)
   3. `top_k: int` (optional, bounded 1-30)
5. `email_search_structured`
   1. `query: str` (required)
   2. `top_k: int` (optional, bounded 1-30)
   3. `sender: str | null` (optional)
   4. `subject: str | null` (optional)
   5. `folder: str | null` (optional)
   6. `cc: str | null` (optional)
   7. `to: str | null` (optional)
   8. `bcc: str | null` (optional)
   9. `has_attachments: bool | null` (optional)
   10. `priority: int | null` (optional, ge=0)
   11. `date_from: str | null` (optional, ISO date)
   12. `date_to: str | null` (optional, ISO date)
   13. `min_score: float | null` (optional, bounded 0.0-1.0)
   14. `rerank: bool` (optional, default false)
   15. `hybrid: bool` (optional, default false)
   16. `topic_id: int | null` (optional, ge=0)
   17. `cluster_id: int | null` (optional, ge=0)
   18. `expand_query: bool` (optional, default false)
6. `email_search_thread`
   1. `conversation_id: str` (required)
   2. `top_k: int` (optional, bounded 1-100)
7. `email_smart_search`
   1. `query: str` (required, 1-500 chars)
   2. `top_k: int` (optional, bounded 1-30)
8. `email_find_similar`
   1. `text: str` (required)
   2. `top_k: int` (optional, bounded 1-30)

### Archive Info

9. `email_list_senders`
   1. `limit: int` (optional, bounded 1-200)
10. `email_list_folders`
    1. no parameters
11. `email_stats`
    1. no parameters
12. `email_query_suggestions`
    1. `query: str` (optional)
    2. `top_k: int` (optional)

### Ingestion

13. `email_ingest`
    1. `olm_path: str` (required) — absolute path to `.olm` file
    2. `max_emails: int | null` (optional, ge=1)
    3. `dry_run: bool` (optional, default false)

### Network Analysis

14. `email_top_contacts`
    1. `email: str` (required)
    2. `top_k: int` (optional)
15. `email_communication_between`
    1. `email_a: str` (required)
    2. `email_b: str` (required)
16. `email_network_analysis`
    1. `top_k: int` (optional)

### Temporal Analysis

17. `email_volume_over_time`
    1. `granularity: str` (optional, one of day/week/month)
18. `email_activity_pattern`
    1. no parameters
19. `email_response_times`
    1. `top_k: int` (optional)

### Entity & NLP

20. `email_search_by_entity`
    1. `entity: str` (required)
    2. `entity_type: str | null` (optional)
    3. `top_k: int` (optional)
21. `email_list_entities`
    1. `entity_type: str | null` (optional)
    2. `top_k: int` (optional)
22. `email_entity_network`
    1. `entity: str` (required)
    2. `top_k: int` (optional)
23. `email_find_people`
    1. `name: str` (required)
    2. `top_k: int` (optional)
24. `email_entity_timeline`
    1. `entity: str` (required)
    2. `granularity: str` (optional, one of day/week/month)

### Thread Intelligence

25. `email_thread_summary`
    1. `query: str` (required)
    2. `top_k: int` (optional)
26. `email_action_items`
    1. `query: str | null` (optional)
    2. `top_k: int` (optional)
27. `email_decisions`
    1. `query: str | null` (optional)
    2. `top_k: int` (optional)

### Topics & Clusters

28. `email_topics`
    1. no parameters
29. `email_search_by_topic`
    1. `topic_id: int` (required, ge=0)
    2. `top_k: int` (optional)
30. `email_keywords`
    1. `sender: str | null` (optional)
    2. `folder: str | null` (optional)
    3. `top_k: int` (optional)
31. `email_clusters`
    1. no parameters
32. `email_cluster_emails`
    1. `cluster_id: int` (required, ge=0)
    2. `top_k: int` (optional)

### Data Quality

33. `email_find_duplicates`
    1. `threshold: float` (optional, bounded 0.0-1.0)
    2. `top_k: int` (optional)
34. `email_language_stats`
    1. no parameters
35. `email_sentiment_overview`
    1. no parameters

### Reporting & Export

36. `email_generate_report`
    1. `output_path: str | null` (optional)
37. `email_export_network`
    1. `output_path: str | null` (optional)
38. `email_writing_analysis`
    1. `sender: str | null` (optional)
    2. `top_k: int` (optional)

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
