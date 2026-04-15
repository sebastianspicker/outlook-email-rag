from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.mcp_models import (
    BehavioralCaseScopeInput,
    BehavioralOrgContextInput,
    CasePartyInput,
    EmailAnswerContextInput,
    ReportingLineInput,
    RoleFactInput,
)


async def test_email_answer_context_emits_case_bundle_and_applies_case_scope_dates(monkeypatch):
    import src.tools.search as search_mod

    class DummyRetriever:
        def search_filtered(self, query, top_k=10, **kwargs):
            assert query == "What happened after the complaint?"
            assert top_k == 1
            assert kwargs["date_from"] == "2026-02-01"
            assert kwargs["date_to"] == "2026-02-28"
            return [
                SimpleNamespace(
                    metadata={
                        "uid": "uid-case-1",
                        "subject": "Re: Complaint follow-up",
                        "sender_email": "manager@example.com",
                        "sender_name": "Morgan Manager",
                        "date": "2026-02-12T10:00:00",
                        "conversation_id": "conv-case-1",
                    },
                    chunk_id="chunk-case-1",
                    text="We should discuss this privately tomorrow morning.",
                    score=0.92,
                )
            ]

    class DummyDB:
        conn = None

        def get_emails_full_batch(self, uids):
            assert uids == ["uid-case-1"]
            return {
                "uid-case-1": {
                    "uid": "uid-case-1",
                    "body_text": "Intro. We should discuss this privately tomorrow morning. Thanks.",
                    "normalized_body_source": "body_text_html",
                    "to": ["Alex Example <alex@example.com>"],
                    "cc": [],
                    "bcc": [],
                    "reply_context_from": "alex@example.com",
                    "reply_context_to_json": "[]",
                    "conversation_id": "conv-case-1",
                }
            }

    class DummyDeps:
        DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available."})

        @staticmethod
        def get_retriever():
            return DummyRetriever()

        @staticmethod
        def get_email_db():
            return DummyDB()

        @staticmethod
        async def offload(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        @staticmethod
        def sanitize(text: str) -> str:
            return text

        @staticmethod
        def tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def write_tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def idempotent_write_annotations(title: str):
            return {"title": title}

    monkeypatch.setattr(search_mod, "_deps", DummyDeps)

    payload = await search_mod.email_answer_context(
        EmailAnswerContextInput(
            question="What happened after the complaint?",
            max_results=1,
            case_scope=BehavioralCaseScopeInput(
                case_label="complaint-follow-up",
                target_person=CasePartyInput(name="Alex Example", email="alex@example.com", role_hint="employee"),
                comparator_actors=[CasePartyInput(name="Pat Peer", email="pat@example.com", role_hint="employee")],
                suspected_actors=[CasePartyInput(name="Morgan Manager", email="manager@example.com", role_hint="manager")],
                date_from="2026-02-01",
                date_to="2026-02-28",
                allegation_focus=["retaliation", "exclusion"],
                analysis_goal="hr_review",
                context_notes="Complaint filed on 2026-02-03.",
                org_context=BehavioralOrgContextInput(
                    role_facts=[
                        RoleFactInput(
                            person=CasePartyInput(name="Morgan Manager", email="manager@example.com"),
                            role_type="manager",
                            title="Head of Unit",
                            department="Operations",
                        )
                    ],
                    reporting_lines=[
                        ReportingLineInput(
                            manager=CasePartyInput(name="Morgan Manager", email="manager@example.com"),
                            report=CasePartyInput(name="Alex Example", email="alex@example.com"),
                        )
                    ],
                ),
            ),
        )
    )
    data = json.loads(payload)

    assert data["search"]["date_from"] == "2026-02-01"
    assert data["search"]["date_to"] == "2026-02-28"
    assert data["case_bundle"]["bundle_id"].startswith("case-")
    assert data["case_bundle"]["scope"]["case_label"] == "complaint-follow-up"
    assert data["case_bundle"]["scope"]["target_person"]["name"] == "Alex Example"
    assert data["case_bundle"]["scope"]["comparator_actors"][0]["email"] == "pat@example.com"
    assert data["case_bundle"]["scope"]["suspected_actors"][0]["email"] == "manager@example.com"
    assert data["case_bundle"]["scope"]["allegation_focus"] == ["retaliation", "exclusion"]
    assert data["case_bundle"]["scope"]["analysis_goal"] == "hr_review"
    assert data["case_bundle"]["scope"]["target_person"]["actor_id"]
    assert data["case_bundle"]["scope"]["suspected_actors"][0]["actor_id"] == data["candidates"][0]["sender_actor_id"]
    assert data["candidates"][0]["sender_actor_resolution"] == {"resolved_by": "email", "ambiguous": False}
    assert data["actor_identity_graph"]["stats"]["actor_count"] == 2
    assert data["actor_identity_graph"]["unresolved_references"] == []
    assert data["power_context"]["org_context_provided"] is True
    assert data["power_context"]["missing_org_context"] is False
    assert data["power_context"]["supplied_role_facts"][0]["role_type"] == "manager"
    assert data["behavioral_taxonomy"]["version"] == "1"
    assert len(data["behavioral_taxonomy"]["categories"]) == 10
    assert data["behavioral_taxonomy"]["focus_category_ids"] == [
        "retaliatory_sequence",
        "escalation_pressure",
        "selective_non_response",
        "exclusion",
        "withholding_information",
    ]
    manager_actor = next(
        actor for actor in data["actor_identity_graph"]["actors"] if actor["primary_email"] == "manager@example.com"
    )
    assert manager_actor["role_context"]["supplied_role_facts"][0]["role_type"] == "manager"


@pytest.mark.asyncio
async def test_email_answer_context_prefers_top_level_dates_over_case_scope(monkeypatch):
    import src.tools.search as search_mod

    class DummyRetriever:
        def search_filtered(self, query, top_k=10, **kwargs):
            assert kwargs["date_from"] == "2026-03-01"
            assert kwargs["date_to"] == "2026-03-31"
            return []

    class DummyDB:
        def get_emails_full_batch(self, uids):
            return {}

    class DummyDeps:
        DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available."})

        @staticmethod
        def get_retriever():
            return DummyRetriever()

        @staticmethod
        def get_email_db():
            return DummyDB()

        @staticmethod
        async def offload(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        @staticmethod
        def sanitize(text: str) -> str:
            return text

        @staticmethod
        def tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def write_tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def idempotent_write_annotations(title: str):
            return {"title": title}

    monkeypatch.setattr(search_mod, "_deps", DummyDeps)

    payload = await search_mod.email_answer_context(
        EmailAnswerContextInput(
            question="What changed in March?",
            max_results=1,
            date_from="2026-03-01",
            date_to="2026-03-31",
            case_scope=BehavioralCaseScopeInput(
                target_person=CasePartyInput(name="Alex Example", email="alex@example.com"),
                date_from="2026-02-01",
                date_to="2026-02-28",
                allegation_focus=["retaliation"],
                analysis_goal="internal_review",
            ),
        )
    )
    data = json.loads(payload)

    assert data["search"]["date_from"] == "2026-03-01"
    assert data["search"]["date_to"] == "2026-03-31"
