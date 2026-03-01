# Email RAG — Search Your Outlook Emails with Claude

A local RAG (Retrieval-Augmented Generation) system that lets Claude search through your Outlook for Mac email archive. Works as both a CLI tool and an MCP server for Claude Code.

## Architecture

```
┌─────────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐
│ Outlook .olm │───▶│  Parser  │───▶│ Embedder │───▶│ ChromaDB  │
│   Export     │    │ (XML→JSON)│   │(MiniLM)  │    │ (Local)   │
└─────────────┘    └──────────┘    └──────────┘    └─────┬─────┘
                                                         │
                                          ┌──────────────┘
                                          ▼
                                   ┌─────────────┐    ┌─────────┐
                                   │  Retriever   │───▶│  Claude  │
                                   │ (search tool)│    │ (answer) │
                                   └─────────────┘    └─────────┘
```

## Quick Start

### 1. Install Dependencies

```bash
cd email-rag
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Export Your Emails from Outlook for Mac

1. Open Outlook for Mac
2. Go to **Tools → Export**
3. Select **Outlook for Mac Data File (.olm)**
4. Choose what to export (Mail is required, others optional)
5. Save the `.olm` file — place it in the `data/` directory

### 3. Ingest Your Emails

```bash
# Parse and embed your .olm file
python -m src.ingest data/your-export.olm
```

This will:
- Extract all emails from the `.olm` archive
- Chunk them for embedding
- Embed them using the local MiniLM model
- Store everything in a local ChromaDB database at `data/chromadb/`

First run downloads the embedding model (~90MB). Subsequent runs are fast.

### 4. Search via CLI

```bash
# Interactive search
python -m src.cli

# Single query
python -m src.cli --query "What did John say about the Q3 budget?"
```

### 5. Use with Claude Code (MCP Server)

Add to your Claude Code MCP config (`~/.claude/claude_desktop_config.json` or equivalent):

```json
{
  "mcpServers": {
    "email_search": {
      "command": "/path/to/email-rag/.venv/bin/python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/email-rag"
    }
  }
}
```

Then in Claude Code, just ask naturally:
> "Search my emails for anything about the server migration from last October"

Claude will call the `email_search` tool, retrieve relevant emails, and answer with context.

## MCP Tools Provided

| Tool | Description |
|------|-------------|
| `email_search` | Semantic search across all emails. Returns the most relevant email chunks for a query. |
| `email_search_by_sender` | Search emails filtered by sender address or name. |
| `email_search_by_date` | Search within a specific date range. |
| `email_list_senders` | List all unique senders in the archive (useful for discovering who's in there). |
| `email_stats` | Get stats about the email archive (count, date range, top senders). |

## Configuration

Set these environment variables or create a `.env` file:

```bash
# Required for Claude-powered answers in CLI mode
ANTHROPIC_API_KEY=sk-ant-...

# Optional
CHROMADB_PATH=data/chromadb          # Where to store the vector DB
EMBEDDING_MODEL=all-MiniLM-L6-v2    # Sentence-transformer model
TOP_K=10                              # Number of results to retrieve
```

## Project Structure

```
email-rag/
├── README.md
├── requirements.txt
├── .env.example
├── data/                  # Your .olm files and ChromaDB storage
│   └── chromadb/          # Auto-created vector database
└── src/
    ├── __init__.py
    ├── parse_olm.py       # .olm archive parser
    ├── chunker.py         # Email → embedding chunks
    ├── embedder.py        # Embedding + ChromaDB storage
    ├── retriever.py       # Search / retrieval logic
    ├── ingest.py          # End-to-end ingestion pipeline
    ├── cli.py             # Interactive CLI with Claude answers
    └── mcp_server.py      # MCP server for Claude Code
```

## Notes

- **Privacy**: Everything runs locally. Your emails never leave your machine (except when sent to Claude API for answering).
- **Embedding model**: `all-MiniLM-L6-v2` runs entirely on your Mac's CPU. No GPU needed.
- **ChromaDB**: Persistent local storage. No server to run.
- **Incremental ingestion**: Running ingest again will skip already-processed emails (deduplication by message ID).
