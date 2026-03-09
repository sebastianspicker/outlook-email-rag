"""Cluster, topic, keyword, and query suggestion MCP tools."""

from __future__ import annotations

import json

from ..mcp_models import (
    ClusterEmailsInput,
    FindSimilarInput,
    QuerySuggestionsInput,
    SearchByTopicInput,
    TopKeywordsInput,
)


def register(mcp, deps) -> None:
    """Register cluster, topic, keyword, and suggestion tools."""

    @mcp.tool(name="email_clusters", annotations=deps.tool_annotations("List Email Clusters"))
    async def email_clusters_tool() -> str:
        """List all email clusters with sizes, representative subjects, and labels.

        Clusters group similar emails together based on embedding similarity.
        Requires clustering during ingestion.

        Returns:
            JSON list of cluster summaries.
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.cluster_summary()
            if not results:
                return json.dumps({"error": "No clusters available. Run ingestion with --cluster."})
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_find_similar", annotations=deps.tool_annotations("Find Similar Emails"))
    async def email_find_similar(params: FindSimilarInput) -> str:
        """Find emails most similar to a given email or query text.

        Provide either uid (to find emails similar to a specific email) or
        query (to find emails similar to a text description).

        Args:
            params: uid or query, top_k.

        Returns:
            JSON list of similar emails with similarity scores.
        """
        def _run():
            if not params.uid and not params.query:
                return json.dumps({"error": "Provide either uid or query."})

            retriever = deps.get_retriever()
            if params.query:
                results = retriever.search(params.query, top_k=params.top_k)
                return deps.sanitize(retriever.format_results_for_claude(results))

            return json.dumps({"error": "UID-based similarity requires embeddings. Use query instead."})
        return await deps.offload(_run)

    @mcp.tool(name="email_cluster_emails", annotations=deps.tool_annotations("Emails in Cluster"))
    async def email_cluster_emails(params: ClusterEmailsInput) -> str:
        """Get emails in a specific cluster, sorted by proximity to centroid.

        Args:
            params: cluster_id (int), limit (int).

        Returns:
            JSON list of emails in the cluster.
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.emails_in_cluster(params.cluster_id, limit=params.limit)
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_topics", annotations=deps.tool_annotations("List Discovered Topics"))
    async def email_topics() -> str:
        """List all discovered topics with labels, top words, and email counts.

        Topics are discovered via NMF topic modeling during ingestion.
        Each topic has an auto-generated label from its top words.

        Returns:
            JSON list of {id, label, top_words, email_count} entries.
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.topic_distribution()
            if not results:
                return json.dumps({"error": "No topics available. Run ingestion with --extract-keywords."})
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_search_by_topic", annotations=deps.tool_annotations("Search Emails by Topic"))
    async def email_search_by_topic(params: SearchByTopicInput) -> str:
        """Find emails assigned to a specific topic, ranked by relevance.

        Args:
            params: topic_id (int) - ID from email_topics, limit (int).

        Returns:
            JSON list of emails with topic weight.
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.emails_by_topic(params.topic_id, limit=params.limit)
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_keywords", annotations=deps.tool_annotations("Top Keywords"))
    async def email_keywords(params: TopKeywordsInput) -> str:
        """Top keywords across the archive or filtered by sender/folder.

        Keywords are extracted via TF-IDF during ingestion.

        Args:
            params: sender (optional), folder (optional), limit (int).

        Returns:
            JSON list of {keyword, avg_score, email_count} entries.
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.top_keywords(sender=params.sender, folder=params.folder, limit=params.limit)
            if not results:
                return json.dumps({"error": "No keywords available. Run ingestion with --extract-keywords."})
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_query_suggestions", annotations=deps.tool_annotations("Query Suggestions"))
    async def email_query_suggestions(params: QuerySuggestionsInput) -> str:
        """Get search suggestions based on indexed email data.

        Returns categorized suggestions including top senders, folders,
        and entities to help discover relevant search queries.
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            from ..query_suggestions import QuerySuggester

            suggester = QuerySuggester(db)
            result = suggester.suggest(limit=params.limit)
            return json.dumps(result, indent=2)
        return await deps.offload(_run)
