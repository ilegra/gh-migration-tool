"""Unit tests for Step 2 Prepare stage."""

from __future__ import annotations

from gh_migration_tool.models import CheckResult, MigrationUnit, UnitStatus
from gh_migration_tool.stages.prepare import prepare_candidates


def _unit(name: str, atomic_group: str | None = None) -> MigrationUnit:
    return MigrationUnit(
        name=name,
        origin_url=f"https://dev.azure.com/org/p/_git/{name}",
        destination_url=f"https://github.com/org/{name}.git",
        atomic_group=atomic_group,
    )


def test_given_all_passing_pre_checks_when_preparing_then_all_become_candidates() -> (
    None
):
    u1, u2 = _unit("repo-a"), _unit("repo-b")
    results = {
        "repo-a": [CheckResult("c1", True, "ok")],
        "repo-b": [CheckResult("c1", True, "ok")],
    }

    candidates, records = prepare_candidates([u1, u2], results)

    assert [c.name for c in candidates] == ["repo-a", "repo-b"]
    assert records["repo-a"].status == UnitStatus.SUCCEEDED
    assert records["repo-b"].status == UnitStatus.SUCCEEDED


def test_given_independent_failure_when_preparing_then_only_failed_unit_excluded() -> (
    None
):
    u1, u2 = _unit("repo-a"), _unit("repo-b")
    results = {
        "repo-a": [CheckResult("c1", False, "jenkins red")],
        "repo-b": [CheckResult("c1", True, "ok")],
    }

    candidates, records = prepare_candidates([u1, u2], results)

    assert [c.name for c in candidates] == ["repo-b"]
    assert records["repo-a"].status == UnitStatus.PRE_CHECK_FAILED
    assert records["repo-b"].status == UnitStatus.SUCCEEDED


def test_given_atomic_group_with_one_failure_when_preparing_then_siblings_blocked() -> (
    None
):
    u1 = _unit("backend", atomic_group="checkout")
    u2 = _unit("frontend", atomic_group="checkout")
    u3 = _unit("docs", atomic_group=None)
    results = {
        "backend": [CheckResult("c1", False, "build failed")],
        "frontend": [CheckResult("c1", True, "ok")],
        "docs": [CheckResult("c1", True, "ok")],
    }

    candidates, records = prepare_candidates([u1, u2, u3], results)

    assert [c.name for c in candidates] == ["docs"]
    assert records["backend"].status == UnitStatus.PRE_CHECK_FAILED
    assert records["frontend"].status == UnitStatus.BLOCKED_BY_GROUP
    assert records["docs"].status == UnitStatus.SUCCEEDED
