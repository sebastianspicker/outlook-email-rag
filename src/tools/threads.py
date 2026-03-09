"""Thread intelligence and smart search MCP tools."""

from __future__ import annotations

import json

from ..mcp_models import (
    ActionItemsInput,
    DecisionsInput,
    SmartSearchInput,
    ThreadSummaryInput,
    ThreadTopicSearchInput,
)


def register(mcp, deps) -> None:
    """Register thread intelligence tools."""

    @mcp.tool(name="email_thread_summary", annotations=deps.tool_annotations("Summarize Thread"))
    async def email_thread_summary(params: ThreadSummaryInput) -> str:
        """Summarize a conversation thread using extractive summarization.

        Selects the most important sentences from the thread based on
        TF-IDF scoring with position bias.

        Args:
            params: conversation_id (str), max_sentences (int).

        Returns:
            JSON with thread summary text.
        """
        retriever = deps.get_retriever()
        results = retriever.search_by_thread(
            conversation_id=params.conversation_id, top_k=50
        )
        if not results:
            return json.dumps({"error": "No emails found for this thread."})

        emails = [
            {"clean_body": r.text, "sender_email": r.metadata.get("sender_email", ""),
             "sender_name": r.metadata.get("sender_name", ""), "date": r.metadata.get("date", ""),
             "uid": r.metadata.get("uid", ""), "subject": r.metadata.get("subject", "")}
            for r in results
        ]

        from ..thread_summarizer import summarize_thread

        summary = summarize_thread(emails, max_sentences=params.max_sentences)
        return json.dumps({"conversation_id": params.conversation_id, "summary": summary})

    @mcp.tool(name="email_action_items", annotations=deps.tool_annotations("Extract Action Items"))
    async def email_action_items(params: ActionItemsInput) -> str:
        """Extract action items from a thread or across recent emails.

        Detects patterns like 'please do X', 'need to', 'I will', 'by Friday'.

        Args:
            params: conversation_id or days, limit.

        Returns:
            JSON list of action items with assignee and deadline.
        """
        from ..thread_intelligence import ThreadAnalyzer

        analyzer = ThreadAnalyzer()

        if params.conversation_id:
            retriever = deps.get_retriever()
            results = retriever.search_by_thread(
                conversation_id=params.conversation_id, top_k=50
            )
            if not results:
                return json.dumps({"error": "No emails found for this thread."})

            all_items = []
            for r in results:
                items = analyzer.extract_action_items(
                    r.text,
                    sender=r.metadata.get("sender_email", ""),
                    source_uid=r.metadata.get("uid", ""),
                )
                all_items.extend(items)

            return json.dumps(
                [{"text": a.text, "assignee": a.assignee, "deadline": a.deadline,
                  "is_urgent": a.is_urgent} for a in all_items[:params.limit]],
                indent=2,
            )

        return json.dumps({"error": "Provide conversation_id to extract action items."})

    @mcp.tool(name="email_decisions", annotations=deps.tool_annotations("Extract Decisions"))
    async def email_decisions(params: DecisionsInput) -> str:
        """Extract decisions from email threads.

        Detects patterns like 'we decided', 'agreed to', 'approved', 'go ahead with'.

        Args:
            params: conversation_id or days.

        Returns:
            JSON list of decisions with who made them and when.
        """
        from ..thread_intelligence import ThreadAnalyzer

        analyzer = ThreadAnalyzer()

        if params.conversation_id:
            retriever = deps.get_retriever()
            results = retriever.search_by_thread(
                conversation_id=params.conversation_id, top_k=50
            )
            if not results:
                return json.dumps({"error": "No emails found for this thread."})

            all_decisions = []
            for r in results:
                decisions = analyzer.extract_decisions(
                    r.text,
                    sender=r.metadata.get("sender_email", ""),
                    date=r.metadata.get("date", ""),
                    source_uid=r.metadata.get("uid", ""),
                )
                all_decisions.extend(decisions)

            return json.dumps(
                [{"text": d.text, "made_by": d.made_by, "date": d.date}
                 for d in all_decisions],
                indent=2,
            )

        return json.dumps({"error": "Provide conversation_id to extract decisions."})

    @mcp.tool(
        name="email_search_by_thread_topic",
        annotations=deps.tool_annotations("Search by Thread Topic"),
    )
    async def email_search_by_thread_topic(params: ThreadTopicSearchInput) -> str:
        """Find all emails sharing a thread topic.

        Thread topics are extracted from OLM metadata and group related
        emails more reliably than conversation_id in some cases.

        Args:
            params: thread_topic (str), limit (int).

        Returns:
            JSON with matching emails sorted by date.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        emails = db.thread_by_topic(params.thread_topic, limit=params.limit)
        return json.dumps({
            "thread_topic": params.thread_topic,
            "emails": emails,
            "count": len(emails),
        }, indent=2)

    @mcp.tool(
        name="email_smart_search",
        annotations=deps.tool_annotations("Smart Search"),
    )
    async def email_smart_search(params: SmartSearchInput) -> str:
        """Auto-routing search that detects intent from query text.

        Automatically detects person names, topics, and entities in the query
        and applies relevant filters. Best for exploratory queries where you
        don't know which filters to use. For precise filtered searches, use
        email_search_structured instead.
        """
        retriever = deps.get_retriever()
        query = params.query
        detected_intent: dict = {}

        # Strategy 1: Always do expanded semantic search
        results = retriever.search_filtered(
            query=query, top_k=params.top_k, expand_query=True,
        )
        detected_intent["expand_query"] = True

        # Strategy 2: If we have entity DB, also search person entities
        db = deps.get_email_db()
        if db:
            try:
                person_results = db.people_in_emails(query, limit=5)
                if person_results:
                    detected_intent["person_matches"] = len(person_results)
            except Exception:
                pass

            # Strategy 3: Check if query matches a known topic
            try:
                topic_dist = db.topic_distribution()
                for topic in topic_dist:
                    label = topic.get("label", "").lower()
                    if any(w in label for w in query.lower().split() if len(w) > 3):
                        detected_intent["topic_match"] = {
                            "id": topic["topic_id"],
                            "label": topic["label"],
                        }
                        break
            except Exception:
                pass

        formatted = retriever.format_results_for_claude(results)
        return json.dumps({
            "query": query,
            "count": len(results),
            "detected_intent": detected_intent,
            "formatted_results": formatted,
        })
