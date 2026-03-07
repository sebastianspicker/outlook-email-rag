"""Tests for email clustering."""

import numpy as np

from src.email_clusterer import EmailClusterer


def _make_embeddings(n_samples: int = 30, n_features: int = 8, n_groups: int = 3):
    """Create synthetic embeddings with clear cluster structure."""
    rng = np.random.RandomState(42)
    embeddings = []
    uids = []
    per_group = n_samples // n_groups

    for g in range(n_groups):
        center = rng.randn(n_features) * 3
        for i in range(per_group):
            emb = center + rng.randn(n_features) * 0.3
            embeddings.append(emb)
            uids.append(f"email_{g}_{i}")

    return np.array(embeddings), uids


class TestEmailClusterer:
    def test_fit_with_fixed_k(self):
        embeddings, uids = _make_embeddings()
        clusterer = EmailClusterer(n_clusters=3)
        clusterer.fit(embeddings, uids)
        assert clusterer.is_fitted

    def test_get_clusters(self):
        embeddings, uids = _make_embeddings()
        clusterer = EmailClusterer(n_clusters=3)
        clusterer.fit(embeddings, uids)
        clusters = clusterer.get_clusters()
        assert len(clusters) == 3
        total_size = sum(c["size"] for c in clusters)
        assert total_size == len(uids)

    def test_cluster_structure(self):
        embeddings, uids = _make_embeddings()
        clusterer = EmailClusterer(n_clusters=3)
        clusterer.fit(embeddings, uids)
        clusters = clusterer.get_clusters()
        for c in clusters:
            assert "cluster_id" in c
            assert "size" in c
            assert "representative_uid" in c
            assert c["representative_uid"] in uids

    def test_get_assignments(self):
        embeddings, uids = _make_embeddings(n_samples=9, n_groups=3)
        clusterer = EmailClusterer(n_clusters=3)
        clusterer.fit(embeddings, uids)
        assignments = clusterer.get_assignments()
        assert len(assignments) == 9
        for uid, cluster_id, distance in assignments:
            assert uid in uids
            assert isinstance(cluster_id, int)
            assert isinstance(distance, float)
            assert distance >= 0

    def test_find_similar(self):
        embeddings, uids = _make_embeddings()
        clusterer = EmailClusterer(n_clusters=3)
        clusterer.fit(embeddings, uids)
        # Find similar to first embedding
        similar = clusterer.find_similar(embeddings[0], top_k=5)
        assert len(similar) == 5
        # Most similar should be the email itself
        assert similar[0][0] == uids[0]
        # Similarity scores should be floats
        for uid, score in similar:
            assert isinstance(score, float)

    def test_find_similar_by_uid(self):
        embeddings, uids = _make_embeddings()
        clusterer = EmailClusterer(n_clusters=3)
        clusterer.fit(embeddings, uids)
        similar = clusterer.find_similar_by_uid(uids[0], top_k=3)
        assert len(similar) == 3
        # Should not include the query email itself
        result_uids = [u for u, _ in similar]
        assert uids[0] not in result_uids

    def test_find_similar_by_uid_unknown(self):
        embeddings, uids = _make_embeddings()
        clusterer = EmailClusterer(n_clusters=3)
        clusterer.fit(embeddings, uids)
        assert clusterer.find_similar_by_uid("nonexistent") == []

    def test_auto_detect_k(self):
        embeddings, uids = _make_embeddings(n_samples=30, n_groups=3)
        clusterer = EmailClusterer()  # No fixed k
        clusterer.fit(embeddings, uids)
        assert clusterer.is_fitted
        clusters = clusterer.get_clusters()
        assert len(clusters) >= 2

    def test_too_few_embeddings(self):
        embeddings = np.array([[1.0, 2.0], [3.0, 4.0]])
        uids = ["a", "b"]
        clusterer = EmailClusterer(n_clusters=3)
        clusterer.fit(embeddings, uids)
        # With only 2 samples, clustering is not meaningful
        assert not clusterer.is_fitted

    def test_single_embedding(self):
        embeddings = np.array([[1.0, 2.0]])
        uids = ["a"]
        clusterer = EmailClusterer()
        clusterer.fit(embeddings, uids)
        assert not clusterer.is_fitted

    def test_not_fitted_returns_empty(self):
        clusterer = EmailClusterer()
        assert clusterer.get_clusters() == []
        assert clusterer.get_assignments() == []
        assert clusterer.find_similar(np.array([1.0, 2.0])) == []


# ── SQLite cluster tests ─────────────────────────────────────


class TestClusterSQLite:
    def _make_db_with_emails(self, n=3):
        from src.email_db import EmailDatabase
        from src.parse_olm import Email

        db = EmailDatabase(":memory:")
        uids = []
        for i in range(n):
            email = Email(
                message_id=f"<c{i}@test>",
                subject=f"Cluster Email {i}",
                sender_name="Alice",
                sender_email="alice@co.com",
                to=["bob@co.com"],
                cc=[],
                bcc=[],
                date=f"2024-01-{i + 10:02d}T10:00:00",
                body_text=f"Content for cluster email {i}",
                body_html="",
                folder="Inbox",
                has_attachments=False,
            )
            db.insert_email(email)
            uids.append(email.uid)
        return db, uids

    def test_insert_and_query_clusters(self):
        db, uids = self._make_db_with_emails(3)
        db.insert_clusters_batch([
            (uids[0], 0, 0.15),
            (uids[1], 0, 0.25),
            (uids[2], 1, 0.10),
        ])
        db.insert_cluster_info([
            {"cluster_id": 0, "size": 2, "representative_uid": uids[0], "label": "group A"},
            {"cluster_id": 1, "size": 1, "representative_uid": uids[2], "label": "group B"},
        ])

        results = db.emails_in_cluster(0)
        assert len(results) == 2

    def test_cluster_summary(self):
        db, uids = self._make_db_with_emails(3)
        db.insert_cluster_info([
            {"cluster_id": 0, "size": 2, "representative_uid": uids[0], "label": "group A"},
            {"cluster_id": 1, "size": 1, "representative_uid": uids[2], "label": "group B"},
        ])
        summary = db.cluster_summary()
        assert len(summary) == 2
        assert summary[0]["size"] >= summary[1]["size"]  # Sorted by size desc
        assert summary[0]["representative_subject"] is not None

    def test_empty_clusters(self):
        from src.email_db import EmailDatabase

        db = EmailDatabase(":memory:")
        assert db.cluster_summary() == []
        assert db.emails_in_cluster(0) == []

    def test_cluster_sorted_by_distance(self):
        db, uids = self._make_db_with_emails(3)
        db.insert_clusters_batch([
            (uids[0], 0, 0.50),
            (uids[1], 0, 0.10),
            (uids[2], 0, 0.30),
        ])
        results = db.emails_in_cluster(0)
        distances = [r["distance"] for r in results]
        assert distances == sorted(distances)


# ── MCP tool tests ───────────────────────────────────────────


class TestMCPClusterTools:
    def test_clusters_tool_importable(self):
        from src.mcp_server import email_clusters_tool

        assert callable(email_clusters_tool)

    def test_find_similar_tool_importable(self):
        from src.mcp_server import email_find_similar

        assert callable(email_find_similar)

    def test_cluster_emails_tool_importable(self):
        from src.mcp_server import email_cluster_emails

        assert callable(email_cluster_emails)

    def test_cluster_emails_input(self):
        from src.mcp_server import ClusterEmailsInput

        inp = ClusterEmailsInput(cluster_id=5, limit=20)
        assert inp.cluster_id == 5
