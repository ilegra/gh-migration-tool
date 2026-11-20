"""Unit tests for Step 2 Prepare stage."""

from __future__ import annotations

from gh_migration_tool.contracts import CheckResult, MigrationUnit, UnitStatus
from gh_migration_tool.engine.prepare import prepare_candidates


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
    c1 = CheckResult("c1", True, "ok")
    c2 = CheckResult("c1", True, "ok")
    results = {"repo-a": [c1], "repo-b": [c2]}

    candidates, records = prepare_candidates([u1, u2], results)

    assert [c.name for c in candidates] == ["repo-a", "repo-b"]
    assert records["repo-a"].unit == u1
    assert records["repo-a"].status == UnitStatus.SUCCEEDED
    assert records["repo-a"].pre_checks == [c1]
    assert records["repo-a"].setup_result is None
    assert records["repo-a"].migration_result is None
    assert records["repo-a"].post_checks == []
    assert records["repo-a"].inventory_result is None
    assert records["repo-a"].error_summary == ""

    assert records["repo-b"].unit == u2
    assert records["repo-b"].status == UnitStatus.SUCCEEDED
    assert records["repo-b"].pre_checks == [c2]
    assert records["repo-b"].error_summary == ""


def test_given_independent_failure_when_preparing_then_only_failed_unit_excluded() -> (
    None
):
    u1, u2 = _unit("repo-a"), _unit("repo-b")
    fail_check = CheckResult("c1", False, "jenkins red")
    pass_check = CheckResult("c1", True, "ok")
    results = {"repo-a": [fail_check], "repo-b": [pass_check]}

    candidates, records = prepare_candidates([u1, u2], results)

    assert [c.name for c in candidates] == ["repo-b"]
    assert records["repo-a"].unit == u1
    assert records["repo-a"].status == UnitStatus.PRE_CHECK_FAILED
    assert records["repo-a"].pre_checks == [fail_check]
    assert records["repo-a"].error_summary == "jenkins red"
    assert records["repo-b"].status == UnitStatus.SUCCEEDED
    assert records["repo-b"].error_summary == ""


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
    assert records["backend"].error_summary == "build failed"
    assert records["frontend"].status == UnitStatus.BLOCKED_BY_GROUP
    assert "checkout" in records["frontend"].error_summary
    assert records["docs"].status == UnitStatus.SUCCEEDED
    assert records["docs"].error_summary == ""


def test_given_empty_units_when_preparing_then_returns_empty_candidates_and_records() -> (
    None
):
    candidates, records = prepare_candidates([], {})

    assert candidates == []
    assert records == {}
