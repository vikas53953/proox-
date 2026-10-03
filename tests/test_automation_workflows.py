"""Stricter checks on the GitHub workflows (review fixes, 2026-10-03).

Kept outside tests/constitution/ so the constitution stays as the owner approved it.
"""

import re

WORKFLOWS = ".github/workflows"


def _workflow_texts(repo_root):
    return {wf.name: wf.read_text() for wf in sorted((repo_root / WORKFLOWS).glob("*.y*ml"))}


def test_workflows_read_only_expected_secrets_in_any_spelling(repo_root):
    allowed = {"claude.yml": {"ANTHROPIC_API_KEY"}}
    for name, text in _workflow_texts(repo_root).items():
        found = set(re.findall(r"secrets(?:\.|\[\s*['\"])([A-Za-z0-9_]+)", text))
        assert found <= allowed.get(name, set()), f"{name}: unexpected secrets {found}"


def test_claude_workflow_owner_gate_is_the_job_condition(repo_root):
    text = (repo_root / WORKFLOWS / "claude.yml").read_text()
    assert re.search(r"if:\s*\|?\s*\n\s*github\.actor == github\.repository_owner &&", text)


def test_claude_workflow_concurrency_is_per_job_not_workflow(repo_root):
    # Workflow-level concurrency also queues skipped runs (any comment), which can cancel a
    # pending owner request. Job-level concurrency applies only after the owner gate passes.
    text = (repo_root / WORKFLOWS / "claude.yml").read_text()
    assert not re.search(r"^concurrency:", text, re.M)
    assert re.search(r"^    concurrency:", text, re.M)


def test_claude_workflow_token_is_read_only_and_not_persisted(repo_root):
    text = (repo_root / WORKFLOWS / "claude.yml").read_text()
    for scope in ("contents", "pull-requests", "issues"):
        assert re.search(rf"^\s+{scope}: read\b", text, re.M), scope
    assert "persist-credentials: false" in text
