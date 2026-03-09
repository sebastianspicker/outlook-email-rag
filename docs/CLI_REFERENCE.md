# CLI Reference

The command-line interface is a standalone way to search and analyze your email archive directly from Terminal. It doesn't require Claude Code — it works entirely on its own.

## Starting the CLI

Make sure the virtual environment is active first:

```bash
source .venv/bin/activate
```

## Interactive mode

Run without arguments to enter an interactive search loop:

```bash
python -m src.cli
```

You'll see a summary of your archive and a search prompt:

```
+-------------- Email RAG ---------------+
| Emails: 1842 | Chunks: 4210 |          |
| Senders: 312 | Range: 2023-01 -> 2024  |
| Type 'quit' to exit, 'stats', ...      |
+-----------------------------------------+

Search: _
```

Type a query, press Enter, and you get a table of results. Type `stats` for archive statistics, `senders` for the top sender list, or `quit` to exit.

## Single query mode

For one-off searches, use `--query` (or `-q`):

```bash
python -m src.cli --query "Q3 budget approval"
```

Output shows each result with relevance score, sender, date, subject, and the matching text:

```
============================================================
Result 1 (relevance: 0.87)
Subject: Re: Q3 Budget Approval
Sender:  finance@company.com
Date:    2024-07-15
Folder:  Inbox

The Q3 budget has been approved with the following allocations...
(~180 tokens)
```

## Filtering results

Combine `--query` with any filter flag to narrow results:

```bash
# By sender (partial match on name or email)
python -m src.cli --query "contract renewal" --sender legal

# By date range (ISO format)
python -m src.cli --query "invoice" \
    --date-from 2024-01-01 \
    --date-to 2024-06-30

# By recipient
python -m src.cli --query "proposal" --to john@example.com

# Only emails with attachments
python -m src.cli --query "report" --has-attachments

# By folder (partial match)
python -m src.cli --query "update" --folder "Sent Items"

# By CC recipient
python -m src.cli --query "review" --cc manager@company.com

# By BCC recipient
python -m src.cli --query "announcement" --bcc board@company.com

# By email type (reply, forward, or original)
python -m src.cli --query "meeting notes" --email-type reply

# By minimum priority level
python -m src.cli --query "urgent" --priority 3

# Combine multiple filters
python -m src.cli --query "contract" \
    --sender legal \
    --date-from 2024-01-01 \
    --has-attachments \
    --min-score 0.5
```

## Search modes

Three search modes provide different trade-offs between speed and accuracy:

```bash
# Standard semantic search (default — fast)
python -m src.cli --query "budget discussion"

# Hybrid: semantic + BM25 keyword search (better for exact terms)
python -m src.cli --query "invoice #INV-2024-0847" --hybrid

# With cross-encoder reranking (slower but most precise)
python -m src.cli --query "budget discussion" --rerank

# Query expansion: auto-adds related terms
python -m src.cli --query "security" --expand-query

# Combine all three
python -m src.cli --query "infrastructure costs" --hybrid --rerank --expand-query

# Filter by discovered topic or cluster
python -m src.cli --query "migration" --topic 5
python -m src.cli --query "hiring" --cluster-id 3
```

## Output format

```bash
# Human-readable text (default)
python -m src.cli --query "budget" --format text

# Machine-readable JSON
python -m src.cli --query "budget" --format json

# Legacy JSON shorthand
python -m src.cli --query "budget" --json

# Control number of results (default: 10, max: 1000)
python -m src.cli --query "budget" --top-k 20
```

## Analytics commands

These don't require a query — they analyze your entire archive:

```bash
# Archive statistics (total emails, chunks, senders, date range)
python -m src.cli --stats

# Top senders (show top 20)
python -m src.cli --list-senders 20

# Top contacts for a specific email address
python -m src.cli --top-contacts user@company.com

# Email volume over time
python -m src.cli --volume day     # daily
python -m src.cli --volume week    # weekly
python -m src.cli --volume month   # monthly

# Activity heatmap (hour x day-of-week)
python -m src.cli --heatmap

# Average response times per sender
python -m src.cli --response-times

# Most frequently mentioned entities (organizations, URLs, etc.)
python -m src.cli --entities
python -m src.cli --entities organization   # filter by type

# Search suggestions based on your data
python -m src.cli --suggest
```

## Browsing emails

Browse your archive page by page for systematic review:

```bash
# Browse newest emails (default: 20 per page)
python -m src.cli --browse

# Navigate pages
python -m src.cli --browse --page 3 --page-size 10

# Filter by folder or sender
python -m src.cli --browse --folder Inbox
python -m src.cli --browse --sender legal@company.com
```

## Exporting emails

Export conversation threads or individual emails as formatted HTML (or PDF with weasyprint):

```bash
# Export a conversation thread by conversation ID
python -m src.cli --export-thread conv_abc123

# Export a single email by UID
python -m src.cli --export-email uid_xyz789

# Choose format (html or pdf) and output path
python -m src.cli --export-thread conv_abc123 --export-format pdf --output thread.pdf
python -m src.cli --export-email uid_xyz789 --output email.html
```

> **PDF support** requires `weasyprint`: `pip install weasyprint`. Without it, export falls back to HTML automatically.

## Evidence management

List, export, and verify evidence items collected through the MCP tools:

```bash
# List all evidence items
python -m src.cli --evidence-list

# Filter by category or minimum relevance
python -m src.cli --evidence-list --category discrimination
python -m src.cli --evidence-list --min-relevance 4

# Export evidence report (HTML by default)
python -m src.cli --evidence-export evidence_report.html

# Export as CSV (for Excel)
python -m src.cli --evidence-export evidence.csv --evidence-export-format csv

# Get evidence statistics (counts by category, verified vs unverified)
python -m src.cli --evidence-stats

# Re-verify all evidence quotes against source emails
python -m src.cli --evidence-verify
```

## Reporting commands

```bash
# Generate an HTML report (default: report.html)
python -m src.cli --generate-report
python -m src.cli --generate-report my_report.html

# Export communication network as GraphML
python -m src.cli --export-network
python -m src.cli --export-network my_network.graphml
```

## Index management

```bash
# Delete and recreate the index (requires --yes)
python -m src.cli --reset-index --yes
```

## Complete flag reference

| Flag | Type | Description |
|------|------|-------------|
| `--query`, `-q` | string | Search query (required for search mode) |
| `--top-k` | int | Number of results (default: 10, max: 1000) |
| `--sender` | string | Filter by sender (partial match on name or email) |
| `--subject` | string | Filter by subject (partial match) |
| `--folder` | string | Filter by folder (partial match) |
| `--cc` | string | Filter by CC recipient (partial match) |
| `--to` | string | Filter by To recipient (partial match) |
| `--bcc` | string | Filter by BCC recipient (partial match) |
| `--has-attachments` | flag | Only emails with attachments |
| `--priority` | int | Minimum priority level |
| `--email-type` | choice | `reply`, `forward`, or `original` |
| `--date-from` | YYYY-MM-DD | Start date (inclusive) |
| `--date-to` | YYYY-MM-DD | End date (inclusive) |
| `--min-score` | float | Minimum relevance score (0.0–1.0) |
| `--rerank` | flag | Re-rank with cross-encoder |
| `--hybrid` | flag | Hybrid semantic + BM25 search |
| `--expand-query` | flag | Expand query with related terms |
| `--topic` | int | Filter by discovered topic ID |
| `--cluster-id` | int | Filter by email cluster ID |
| `--format` | choice | Output format: `text` or `json` |
| `--json` | flag | Shorthand for `--format json` |
| `--stats` | flag | Print archive statistics |
| `--list-senders` | int | List top N senders |
| `--top-contacts` | string | Top contacts for an email address |
| `--volume` | choice | Email volume: `day`, `week`, or `month` |
| `--entities` | string? | List top entities (optionally by type) |
| `--heatmap` | flag | Activity heatmap |
| `--response-times` | flag | Response time statistics |
| `--suggest` | flag | Search suggestions |
| `--browse` | flag | Browse emails page by page |
| `--page` | int | Page number for `--browse` (default: 1) |
| `--page-size` | int | Emails per page for `--browse` (default: 20) |
| `--export-thread` | string | Export a conversation thread by conversation ID |
| `--export-email` | string | Export a single email by UID |
| `--export-format` | choice | Export format: `html` or `pdf` (default: `html`) |
| `--output`, `-o` | string | Output file path for exports |
| `--generate-report` | string? | Generate HTML report (default: `report.html`) |
| `--export-network` | string? | Export GraphML network (default: `network.graphml`) |
| `--evidence-list` | flag | List all evidence items |
| `--evidence-export` | string | Export evidence report to file |
| `--evidence-export-format` | choice | Evidence export format: `html`, `csv`, or `pdf` (default: `html`) |
| `--evidence-stats` | flag | Show evidence collection statistics |
| `--evidence-verify` | flag | Re-verify all evidence quotes against source emails |
| `--category` | string | Filter evidence by category |
| `--min-relevance` | int | Filter evidence by minimum relevance (1-5) |
| `--dossier` | string | Generate proof dossier and write to file |
| `--dossier-format` | choice | Dossier format: `html` or `pdf` (default: `html`) |
| `--custody-chain` | flag | View chain-of-custody audit trail |
| `--provenance` | string | View email provenance by UID (OLM source hash, ingestion run, custody events) |
| `--generate-training-data` | string? | Generate contrastive triplets from threads (default: `training_data.jsonl`) |
| `--fine-tune` | string? | Fine-tune embedding model on generated triplets |
| `--fine-tune-output` | string | Output directory for fine-tuned model (default: `models/fine-tuned`) |
| `--fine-tune-epochs` | int | Number of fine-tuning epochs (default: 3) |
| `--reembed` | flag | Rebuild ChromaDB embeddings from corrected body text in SQLite |
| `--reset-index` | flag | Delete and recreate the index (requires `--yes`) |
| `--yes` | flag | Confirm destructive operations |
| `--chromadb-path` | string | Override ChromaDB path |
| `--log-level` | string | Logging level (`DEBUG`, `INFO`, etc.) |
| `--version` | flag | Print version and exit |

## Subcommands (recommended)

The CLI also supports a modern subcommand syntax with better discoverability. Legacy flat-flag syntax continues to work but emits a deprecation warning.

```bash
# Search
python -m src.cli search "Q3 budget" --sender finance
python -m src.cli search --query "contract renewal" --date-from 2024-01-01 --rerank

# Browse
python -m src.cli browse --folder Inbox --page 2 --page-size 30

# Export
python -m src.cli export thread CONV_ID --format pdf --output thread.pdf
python -m src.cli export email UID --output email.html
python -m src.cli export report --output report.html
python -m src.cli export network --output network.graphml

# Evidence
python -m src.cli evidence list --category discrimination --min-relevance 4
python -m src.cli evidence export evidence_report.html --format html
python -m src.cli evidence stats
python -m src.cli evidence verify
python -m src.cli evidence dossier dossier.html --format pdf
python -m src.cli evidence custody
python -m src.cli evidence provenance UID

# Analytics
python -m src.cli analytics stats
python -m src.cli analytics senders 20
python -m src.cli analytics contacts user@company.com
python -m src.cli analytics volume month
python -m src.cli analytics entities --type organization
python -m src.cli analytics heatmap
python -m src.cli analytics response-times

# Training
python -m src.cli training generate-data triplets.jsonl
python -m src.cli training fine-tune triplets.jsonl --epochs 5

# Admin
python -m src.cli admin reset-index --yes
```

Run `python -m src.cli <subcommand> --help` for subcommand-specific help.
