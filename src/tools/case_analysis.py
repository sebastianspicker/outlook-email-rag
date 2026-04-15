"""Dedicated workplace case-analysis MCP tool."""

from __future__ import annotations

from typing import Any

from ..case_analysis import build_case_analysis
from ..case_full_pack import execute_case_full_pack
from ..case_prompt_intake import build_case_prompt_preflight
from ..mcp_models import EmailCaseAnalysisInput, EmailCaseFullPackInput, EmailCasePromptPreflightInput
from .utils import ToolDepsProto, get_deps, json_response

_deps: ToolDepsProto | None = None


def _d() -> ToolDepsProto:
    return get_deps(_deps)


async def email_case_analysis(params: EmailCaseAnalysisInput) -> str:
    """Run one exploratory retrieval-bounded workplace case-analysis workflow."""
    return await build_case_analysis(_d(), params)


async def email_case_prompt_preflight(params: EmailCasePromptPreflightInput) -> str:
    """Return a bounded draft intake plus missing-input guidance for prompt-only matter descriptions."""
    return json_response(build_case_prompt_preflight(params))


async def email_case_full_pack(params: EmailCaseFullPackInput) -> str:
    """Compile and execute the full-pack workflow from a matter prompt plus supplied materials."""
    return json_response(await execute_case_full_pack(_d(), params))


def register(mcp_instance: Any, deps: ToolDepsProto) -> None:
    """Register the dedicated case-analysis MCP tool."""
    global _deps
    _deps = deps
    mcp_instance.tool(
        name="email_case_analysis_exploratory",
        annotations=deps.tool_annotations("Exploratory Workplace Case Analysis"),
    )(email_case_analysis)
    mcp_instance.tool(
        name="email_case_analysis",
        annotations=deps.tool_annotations("Exploratory Workplace Case Analysis (Compatibility Alias)"),
    )(email_case_analysis)
    mcp_instance.tool(name="email_case_prompt_preflight", annotations=deps.tool_annotations("Case Prompt Preflight"))(
        email_case_prompt_preflight
    )
    mcp_instance.tool(name="email_case_full_pack", annotations=deps.idempotent_write_annotations("Case Full Pack"))(
        email_case_full_pack
    )
