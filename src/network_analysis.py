"""Communication network analysis using NetworkX."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .email_db import EmailDatabase

logger = logging.getLogger(__name__)


class CommunicationNetwork:
    """Graph-based analysis of email communication patterns."""

    def __init__(self, email_db: EmailDatabase) -> None:
        self._db = email_db
        self._graph = None

    def top_contacts(self, email_address: str, limit: int = 20) -> list[dict[str, Any]]:
        """Top communication partners (bidirectional frequency)."""
        return self._db.top_contacts(email_address, limit=limit)

    def communication_between(self, email_a: str, email_b: str) -> dict[str, Any]:
        """Bidirectional communication stats between two addresses."""
        return self._db.communication_between(email_a, email_b)

    def network_analysis(self, top_n: int = 20) -> dict[str, Any]:
        """Build directed graph and compute centrality metrics."""
        try:
            import networkx as nx
        except ImportError:
            return {"error": "networkx not installed. Run: pip install networkx"}

        if self._graph is None:
            self._graph = nx.DiGraph()
            for sender, recipient, count in self._db.all_edges():
                self._graph.add_edge(sender, recipient, weight=count)

        if self._graph.number_of_nodes() == 0:
            return {
                "total_nodes": 0,
                "total_edges": 0,
                "most_connected": [],
                "communities": [],
                "bridge_nodes": [],
            }

        # Degree centrality (most connected)
        degree = nx.degree_centrality(self._graph)
        most_connected = sorted(degree.items(), key=lambda x: x[1], reverse=True)[:top_n]

        # Betweenness centrality (bridge nodes)
        try:
            betweenness = nx.betweenness_centrality(self._graph, weight="weight")
            bridge_nodes = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:top_n]
        except Exception:
            bridge_nodes = []

        # Community detection
        communities: list[list[str]] = []
        try:
            undirected = self._graph.to_undirected()
            if hasattr(nx.community, "louvain_communities"):
                comms = nx.community.louvain_communities(undirected, seed=42)
            else:
                comms = nx.community.label_propagation_communities(undirected)
            communities = [sorted(c) for c in comms]
            communities.sort(key=len, reverse=True)
        except Exception:
            logger.debug("Community detection failed", exc_info=True)

        return {
            "total_nodes": self._graph.number_of_nodes(),
            "total_edges": self._graph.number_of_edges(),
            "most_connected": [
                {"email": email, "centrality": round(score, 4)} for email, score in most_connected
            ],
            "communities": communities[:top_n],
            "bridge_nodes": [
                {"email": email, "betweenness": round(score, 4)} for email, score in bridge_nodes
            ],
        }
