"""Compatibility facade for realistic legal-support acceptance fixtures."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

from . import legal_support_acceptance_cases as _cases
from . import legal_support_acceptance_projection as _projection
from .case_full_pack import execute_case_full_pack
from .legal_support_acceptance_context import build_fixture_answer_context

FIXTURE_CASES = _cases.FIXTURE_CASES
FIXTURE_ROOT = _cases.FIXTURE_ROOT
FullPackAcceptanceCase = _cases.FullPackAcceptanceCase
acceptance_case = _cases.acceptance_case
acceptance_case_dir = _cases.acceptance_case_dir
acceptance_case_ids = _cases.acceptance_case_ids
build_fixture_full_pack_input = _cases.build_fixture_full_pack_input
build_golden_projection = _projection.build_golden_projection


async def execute_fixture_full_pack(
    case_id: str,
    *,
    output_path: str | None = None,
    blocked: bool = False,
    compile_only: bool = False,
) -> dict[str, Any]:
    """Execute the real full-pack workflow for one deterministic realistic fixture case."""

    async def _fake_build_answer_context(_deps: Any, _params: Any) -> str:
        return json.dumps(build_fixture_answer_context(case_id))

    async def _fake_build_answer_context_payload(_deps: Any, _params: Any) -> dict[str, Any]:
        return build_fixture_answer_context(case_id)

    class _FixtureDeps:
        @staticmethod
        def get_email_db() -> None:
            return None

    params = build_fixture_full_pack_input(
        case_id,
        output_path=output_path,
        blocked=blocked,
        compile_only=compile_only,
    )
    with (
        patch("src.tools.search_answer_context.build_answer_context", _fake_build_answer_context),
        patch("src.tools.search_answer_context.build_answer_context_payload", _fake_build_answer_context_payload),
    ):
        return await execute_case_full_pack(_FixtureDeps(), params)


def execute_fixture_full_pack_sync(
    case_id: str,
    *,
    output_path: str | None = None,
    blocked: bool = False,
    compile_only: bool = False,
) -> dict[str, Any]:
    """Synchronous wrapper for script-driven acceptance/golden refresh flows."""
    return asyncio.run(
        execute_fixture_full_pack(
            case_id,
            output_path=output_path,
            blocked=blocked,
            compile_only=compile_only,
        )
    )
