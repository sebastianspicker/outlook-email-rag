"""
Interactive CLI for searching emails with Claude-powered answers.

Usage:
    python -m src.cli                           # Interactive mode
    python -m src.cli --query "find budget emails"  # Single query
    python -m src.cli --raw --query "test"      # Raw results, no Claude
"""

import argparse
import os
import json
import sys

from dotenv import load_dotenv

from .retriever import EmailRetriever

load_dotenv()


def ask_claude(query: str, context: str) -> str:
    """Send query + retrieved context to Claude for a synthesized answer."""
    try:
        import anthropic
    except ImportError:
        return "(Install 'anthropic' package and set ANTHROPIC_API_KEY for Claude-powered answers)"

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "(Set ANTHROPIC_API_KEY in .env for Claude-powered answers)"

    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = """You are an email search assistant. The user is searching their personal email archive.
You will receive retrieved email excerpts as context. Use them to answer the user's question.

Guidelines:
- Answer based ONLY on the provided email context. If the emails don't contain the answer, say so.
- Reference specific emails by sender, date, and subject when relevant.
- Be concise but thorough.
- If multiple emails are relevant, synthesize the information.
- Mention if the results seem incomplete and suggest refining the search."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"My question: {query}\n\nRetrieved emails:\n{context}",
        }],
    )

    return message.content[0].text


def run_interactive(retriever: EmailRetriever, use_claude: bool = True, top_k: int = 10):
    """Run interactive search loop."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown

    console = Console()

    # Show stats
    stats = retriever.stats()
    console.print(Panel(
        f"📧 Emails: {stats.get('total_emails', 0)} | "
        f"📦 Chunks: {stats.get('total_chunks', 0)} | "
        f"👤 Senders: {stats.get('unique_senders', 0)} | "
        f"📅 {stats.get('date_range', {}).get('earliest', '?')} → {stats.get('date_range', {}).get('latest', '?')}",
        title="Email RAG",
        subtitle="Type 'quit' to exit, 'stats' for details, 'senders' to list senders",
    ))

    while True:
        try:
            query = console.input("\n[bold cyan]Search:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            break
        if query.lower() == "stats":
            console.print_json(json.dumps(retriever.stats(), indent=2))
            continue
        if query.lower() == "senders":
            senders = retriever.list_senders(30)
            for s in senders:
                console.print(f"  {s['count']:>4}x  {s['name']} <{s['email']}>")
            continue

        # Search
        results = retriever.search(query, top_k=top_k)

        if not results:
            console.print("[yellow]No matching emails found.[/]")
            continue

        # Show raw results summary
        console.print(f"\n[dim]Found {len(results)} results:[/]")
        for i, r in enumerate(results[:5], 1):
            m = r.metadata
            console.print(
                f"  [dim]{i}.[/] [{r.score:.0%}] "
                f"[bold]{m.get('subject', '(no subject)')}[/] "
                f"— {m.get('sender_name', m.get('sender_email', '?'))} "
                f"({m.get('date', '?')[:10]})"
            )
        if len(results) > 5:
            console.print(f"  [dim]...and {len(results) - 5} more[/]")

        # Get Claude's answer
        if use_claude:
            console.print("\n[dim]Asking Claude...[/]")
            context = retriever.format_results_for_claude(results)
            answer = ask_claude(query, context)
            console.print(Panel(Markdown(answer), title="Claude's Answer", border_style="green"))


def run_single_query(retriever: EmailRetriever, query: str, raw: bool = False, top_k: int = 10):
    """Run a single query and print results."""
    results = retriever.search(query, top_k=top_k)

    if raw or not os.getenv("ANTHROPIC_API_KEY"):
        # Print raw results
        for i, r in enumerate(results, 1):
            print(f"\n{'='*60}")
            print(f"Result {i} (relevance: {r.score:.2f})")
            print(r.to_context_string())
    else:
        context = retriever.format_results_for_claude(results)
        answer = ask_claude(query, context)
        print(answer)


def main():
    parser = argparse.ArgumentParser(description="Search your email archive with Claude.")
    parser.add_argument("--query", "-q", help="Single query (omit for interactive mode).")
    parser.add_argument("--raw", action="store_true", help="Show raw results without Claude.")
    parser.add_argument("--top-k", type=int, default=10, help="Number of results to retrieve.")
    parser.add_argument("--chromadb-path", default=None, help="Custom ChromaDB path.")

    args = parser.parse_args()

    retriever = EmailRetriever(chromadb_path=args.chromadb_path)

    if retriever.collection.count() == 0:
        print("❌ No emails in database. Run ingestion first:")
        print("   python -m src.ingest data/your-export.olm")
        sys.exit(1)

    if args.query:
        run_single_query(retriever, args.query, raw=args.raw, top_k=args.top_k)
    else:
        run_interactive(retriever, use_claude=not args.raw, top_k=args.top_k)


if __name__ == "__main__":
    main()
