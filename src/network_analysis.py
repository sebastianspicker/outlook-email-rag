"""Communication network analysis using NetworkX."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
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
        nx = self._ensure_graph()
        if nx is None:
            return {"error": "networkx not installed. Run: pip install networkx"}

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

    def _ensure_graph(self):
        """Build the graph lazily if needed."""
        try:
            import networkx as nx
        except ImportError:
            return None

        if self._graph is None:
            self._graph = nx.DiGraph()
            for sender, recipient, count in self._db.all_edges():
                self._graph.add_edge(sender, recipient, weight=count)
        return nx

    def find_paths(
        self, source: str, target: str, max_hops: int = 3, top_k: int = 5
    ) -> list[dict[str, Any]]:
        """Find shortest communication paths between two email addresses.

        Returns:
            List of {"nodes": [str], "edges": [{"from": str, "to": str, "weight": int}], "hops": int}
        """
        nx = self._ensure_graph()
        if nx is None:
            return [{"error": "networkx not installed"}]

        if self._graph.number_of_nodes() == 0:
            return []

        undirected = self._graph.to_undirected()
        if source not in undirected or target not in undirected:
            return []

        paths = []
        try:
            for path_nodes in nx.shortest_simple_paths(undirected, source, target):
                if len(path_nodes) - 1 > max_hops:
                    break
                edges = []
                for i in range(len(path_nodes) - 1):
                    a, b = path_nodes[i], path_nodes[i + 1]
                    weight = 0
                    if self._graph.has_edge(a, b):
                        weight += self._graph[a][b].get("weight", 0)
                    if self._graph.has_edge(b, a):
                        weight += self._graph[b][a].get("weight", 0)
                    edges.append({"from": a, "to": b, "weight": weight})

                paths.append({
                    "nodes": list(path_nodes),
                    "edges": edges,
                    "hops": len(path_nodes) - 1,
                })
                if len(paths) >= top_k:
                    break
        except nx.NetworkXNoPath:
            pass

        return paths

    def shared_recipients(
        self, email_addresses: list[str], min_shared: int = 2
    ) -> list[dict[str, Any]]:
        """Find recipients who received emails from ALL specified senders.

        Delegates to EmailDatabase SQL query for accuracy.

        Returns:
            List of {"recipient": str, "senders": [str], "total_emails": int}
        """
        return self._db.shared_recipients_query(email_addresses, min_shared=min_shared)

    def coordinated_timing(
        self,
        email_addresses: list[str],
        window_hours: int = 24,
        min_events: int = 3,
    ) -> list[dict[str, Any]]:
        """Detect time windows where multiple senders were active simultaneously.

        Returns:
            List of {"window_start": str, "window_end": str, "senders": [str], "email_count": int}
        """
        if len(email_addresses) < 2:
            return []

        timeline = self._db.sender_activity_timeline(email_addresses)
        if not timeline:
            return []

        # Parse dates
        dated_events = []
        for entry in timeline:
            try:
                dt = datetime.fromisoformat(entry["date"].replace("Z", "+00:00"))
                dated_events.append((dt, entry["sender_email"]))
            except (ValueError, TypeError):
                continue

        if not dated_events:
            return []

        dated_events.sort(key=lambda x: x[0])
        window_delta = timedelta(hours=window_hours)

        results: list[dict[str, Any]] = []
        i = 0
        while i < len(dated_events):
            window_start = dated_events[i][0]
            window_end = window_start + window_delta

            senders_in_window: set[str] = set()
            count = 0
            j = i
            while j < len(dated_events) and dated_events[j][0] <= window_end:
                senders_in_window.add(dated_events[j][1])
                count += 1
                j += 1

            if len(senders_in_window) >= 2 and count >= min_events:
                results.append({
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "senders": sorted(senders_in_window),
                    "email_count": count,
                })
                i = j  # Skip past window
            else:
                i += 1

        return results

    def relationship_summary(
        self, email_address: str, limit: int = 20
    ) -> dict[str, Any]:
        """Comprehensive profile for a single email address.

        Returns:
            {"email": str, "top_contacts": [...], "community": [str],
             "bridge_score": float, "send_count": int, "receive_count": int}
        """
        top_contacts = self.top_contacts(email_address, limit=limit)

        nx = self._ensure_graph()
        bridge_score = 0.0
        community: list[str] = []
        send_count = 0
        receive_count = 0

        if nx and self._graph.number_of_nodes() > 0 and email_address in self._graph:
            # Bridge score
            try:
                betweenness = nx.betweenness_centrality(self._graph, weight="weight")
                bridge_score = round(betweenness.get(email_address, 0.0), 4)
            except Exception:
                pass

            # Community
            try:
                undirected = self._graph.to_undirected()
                if hasattr(nx.community, "louvain_communities"):
                    comms = nx.community.louvain_communities(undirected, seed=42)
                else:
                    comms = nx.community.label_propagation_communities(undirected)
                for comm in comms:
                    if email_address in comm:
                        community = sorted(comm)
                        break
            except Exception:
                pass

            # Send/receive counts from graph edges
            send_count = sum(
                data.get("weight", 0)
                for _, _, data in self._graph.out_edges(email_address, data=True)
            )
            receive_count = sum(
                data.get("weight", 0)
                for _, _, data in self._graph.in_edges(email_address, data=True)
            )

        return {
            "email": email_address,
            "top_contacts": top_contacts,
            "community": community,
            "bridge_score": bridge_score,
            "send_count": send_count,
            "receive_count": receive_count,
        }

    def export_graphml(self, output_path: str) -> dict[str, Any]:
        """Export the communication graph as GraphML for external tools.

        The GraphML format is supported by Gephi, Cytoscape, and other
        network visualization tools.

        Args:
            output_path: File path to write the .graphml file.

        Returns:
            Dict with node/edge counts and the output path.
        """
        nx = self._ensure_graph()
        if nx is None:
            return {"error": "networkx not installed. Run: pip install networkx"}

        from pathlib import Path

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        nx.write_graphml(self._graph, output_path)

        return {
            "output_path": output_path,
            "total_nodes": self._graph.number_of_nodes(),
            "total_edges": self._graph.number_of_edges(),
        }
