"""Verify the four prompt builders share a coherent header layout.

The shared `_build_prompt_header` helper must put `_SYSTEM_HEADER`, the role
label, the user task, and the project context in the same relative order for
every builder. Catches silent drift if a future edit reorders or drops a
header section in one builder but not the others.
"""

from __future__ import annotations

import pytest

from dialectic.core import (
    _SYSTEM_HEADER,
    _build_reviewer_critique_prompt,
    _build_reviewer_rebuttal_prompt,
    _build_writer_initial_prompt,
    _build_writer_revision_prompt,
)
from dialectic.protocol import (
    CritiqueItem,
    ReviewerCritique,
    ReviewerVerdict,
    Severity,
    WriterAction,
    WriterItemResponse,
    WriterReport,
    WriterResponseBundle,
)

USER_PROMPT = "Refactor module X to do Y — header-order sentinel"
PROJECT_CONTEXT = "Project context sentinel: prefer integration tests over mocks."
DIFF = "diff --git a/x b/x\n@@ -0,0 +1,1 @@\n+test\n"

_WRITER_REPORT = WriterReport(diff=DIFF, summary="initial draft")
_CRITIQUE = ReviewerCritique(
    verdict=ReviewerVerdict.REVISE,
    items=[CritiqueItem(id=1, severity=Severity.HIGH, issue="something")],
    summary="needs work",
)
_RESPONSES = WriterResponseBundle(
    responses=[
        WriterItemResponse(item_id=1, action=WriterAction.REJECT, rationale="because"),
    ],
    revised_diff=DIFF,
    revised_diff_summary="no functional change",
)


def _prompts() -> dict[str, tuple[str, str]]:
    """Return {builder_name: (prompt, expected_role_line)} for all four builders."""
    return {
        "writer_initial": (
            _build_writer_initial_prompt(USER_PROMPT, PROJECT_CONTEXT),
            "ROLE: writer",
        ),
        "reviewer_critique": (
            _build_reviewer_critique_prompt(
                USER_PROMPT,
                _WRITER_REPORT,
                DIFF,
                PROJECT_CONTEXT,
            ),
            "ROLE: reviewer",
        ),
        "writer_revision": (
            _build_writer_revision_prompt(
                USER_PROMPT,
                _WRITER_REPORT,
                _CRITIQUE,
                DIFF,
                PROJECT_CONTEXT,
            ),
            "ROLE: writer (revision pass)",
        ),
        "reviewer_rebuttal": (
            _build_reviewer_rebuttal_prompt(
                USER_PROMPT,
                _WRITER_REPORT,
                _CRITIQUE,
                _RESPONSES,
                DIFF,
                PROJECT_CONTEXT,
            ),
            "ROLE: reviewer (rebuttal pass)",
        ),
    }


@pytest.mark.parametrize("name", list(_prompts()))
def test_shared_header_present_and_ordered(name: str) -> None:
    prompt, role_line = _prompts()[name]
    idx_header = prompt.find(_SYSTEM_HEADER)
    idx_role = prompt.find(role_line)
    idx_task = prompt.find(USER_PROMPT)
    idx_context = prompt.find(PROJECT_CONTEXT)
    assert idx_header != -1, f"{name}: missing _SYSTEM_HEADER"
    assert idx_role != -1, f"{name}: missing role line {role_line!r}"
    assert idx_task != -1, f"{name}: missing user task"
    assert idx_context != -1, f"{name}: missing project context"
    # The header order must be: system header → role → task → context.
    assert idx_header < idx_role < idx_task < idx_context, (
        f"{name}: header order violated "
        f"(header={idx_header}, role={idx_role}, task={idx_task}, context={idx_context})"
    )


@pytest.mark.parametrize("name", list(_prompts()))
def test_task_label_correct_for_role(name: str) -> None:
    """Initial writer uses TASK FROM USER; everyone else uses ORIGINAL TASK."""
    prompt, _ = _prompts()[name]
    if name == "writer_initial":
        assert "TASK FROM USER:" in prompt
        assert "ORIGINAL TASK:" not in prompt
    else:
        assert "ORIGINAL TASK:" in prompt
        assert "TASK FROM USER:" not in prompt
