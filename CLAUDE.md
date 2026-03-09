# Email RAG — Instructions for Claude

You have access to a local email archive via 67 MCP tools under the `email_search` server. The archive contains the user's Outlook emails indexed with BGE-M3 embeddings, stored in ChromaDB (vectors) and SQLite (metadata). Everything runs locally — no data leaves the machine.

## How to Search

Start broad, then narrow down:

- `email_search` — general semantic search (start here for most queries)
- `email_search_by_sender` — when the user names a specific person
- `email_search_by_date` — when a time period matters
- `email_search_by_recipient` — search by To field
- `email_search_structured` — power search combining sender, subject, folder, CC, To, BCC, date range, attachment filter, priority, email type, topic, cluster, reranking, and hybrid search
- `email_smart_search` — auto-detects intent (person names, topics) and routes accordingly
- `email_find_similar` — find emails similar to a known one (great for pattern discovery)
- `email_search_by_entity` — find emails mentioning an organization, URL, or phone number
- `email_find_people` — search by person name mentioned in email bodies
- `email_search_thread` — retrieve all emails in a conversation thread

## How to Analyze

Once you find relevant emails, dig deeper:

- `email_thread_summary` — summarize a conversation thread
- `email_action_items` — extract action items and assignments from threads
- `email_decisions` — extract decisions made in threads
- `email_top_contacts` — find top communication partners
- `email_communication_between` — bidirectional stats between two people
- `email_network_analysis` — centrality, communities, bridge nodes
- `relationship_paths` — find communication paths between two people through intermediaries
- `shared_recipients` — identify recipients common to multiple senders
- `coordinated_timing` — detect time windows where multiple senders were simultaneously active
- `relationship_summary` — one-call profile: top contacts, community, bridge score, send/receive ratio
- `email_volume_over_time` — email volume trends (day/week/month)
- `email_activity_pattern` — activity heatmap (hour vs day-of-week)
- `email_response_times` — average response times per sender
- `email_writing_analysis` — writing style and readability metrics per sender
- `email_entity_timeline` — track how often an entity appears over time

## How to Collect Evidence

The evidence system lets users mark emails and quotes as evidence items with categories, relevance scores, and chain-of-custody tracking.

1. **Add evidence:** `evidence_add` with the email UID, the exact quote, a category, a brief summary, relevance score (1-5), and optional notes. The system auto-verifies the quote against the source email.
2. **Batch add:** `evidence_add_batch` for up to 20 items at once.
3. **Review:** `evidence_list` to see all collected evidence (filter by category, relevance, email UID).
4. **Update:** `evidence_update` to refine category, quote, summary, relevance, or notes.
5. **Verify:** `evidence_verify` to re-verify all quotes against source emails.
6. **Timeline:** `evidence_timeline` to view evidence chronologically.
7. **Search:** `evidence_search` to search within evidence items by text.

### Evidence Categories

Use these canonical categories:

- `bossing` — intimidation, power abuse, unreasonable demands
- `harassment` — hostile behavior, bullying, unwanted conduct
- `discrimination` — unequal treatment based on protected characteristics
- `retaliation` — punishment for reporting or complaining
- `hostile_environment` — toxic workplace patterns
- `micromanagement` — excessive control, undermining autonomy
- `exclusion` — deliberate isolation from meetings, decisions, information
- `gaslighting` — denying facts, rewriting history, questioning competence
- `workload` — unreasonable assignments, impossible deadlines
- `general` — other relevant evidence

### Relevance Scores

- **5** — direct proof, strongest evidence
- **4** — strong evidence, clear pattern
- **3** — supporting evidence, adds context
- **2** — background information, minor relevance
- **1** — tangential, worth preserving

## How to Export and Report

- `evidence_export` — export evidence collection as HTML report or CSV
- `dossier_generate` — generate comprehensive proof dossier (HTML/PDF) combining evidence, source emails, relationship analysis, and chain of custody
- `dossier_preview` — preview dossier contents before generating
- `email_export_thread` — export a conversation thread as formatted HTML/PDF
- `email_export_single` — export a single email as formatted HTML/PDF
- `email_generate_report` — generate an HTML overview report of the entire archive
- `email_export_network` — export communication network as GraphML for Gephi/Cytoscape

## Chain of Custody

Every action is logged with SHA-256 hashes and timestamps:

- `custody_chain` — view the audit trail (filter by email UID, event type, date range)
- `email_provenance` — full provenance for an email: OLM source hash, ingestion run, custody events
- `evidence_provenance` — full chain for an evidence item: details + source email provenance + history

## Browsing and Reading

- `email_get_full` — read the complete body of a specific email by UID
- `email_browse` — page through emails (with sender/date/folder filters)
- `email_search_thread` — retrieve all emails in a conversation thread

## Archive Overview

- `email_stats` — total counts, date range, senders, folders
- `email_list_senders` — top senders by frequency
- `email_list_folders` — all folders with counts
- `email_topics` — discovered topics with labels
- `email_clusters` — email clusters with sizes
- `email_keywords` — top keywords (global or per sender/folder)
- `email_language_stats` — language distribution
- `email_sentiment_overview` — sentiment distribution
- `email_find_duplicates` — find near-duplicate emails

## Diagnostics

- `email_model_info` — show embedding model, backend, device, sparse and ColBERT status
- `email_sparse_status` — show sparse vector index status
- `email_ingest` — trigger ingestion of an .olm file from within Claude
- `email_reingest_bodies` — backfill full body text for older emails
- `email_reembed` — rebuild ChromaDB embeddings from corrected body text in SQLite

## Tips

- Always cite the email UID, sender, date, and subject when presenting results.
- Use `email_find_similar` after finding one relevant email — it surfaces patterns.
- Use `relationship_paths` and `coordinated_timing` to show connections between people.
- Use `email_entity_timeline` to show trends over time.
- When the user asks to collect evidence, search first, then offer to mark relevant results.
- When building a dossier, collect evidence items first — the dossier generator pulls from the evidence collection automatically.
- The evidence system tracks who added each item and when; each quote is verified against the source email body.
