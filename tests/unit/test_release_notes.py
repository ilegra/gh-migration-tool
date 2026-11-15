"""Unit tests for release_notes generator."""

from __future__ import annotations

from gh_migration_tool.release_notes import generate_notes


def test_given_conventional_commits_when_generating_notes_then_categorizes_sections() -> (
    None
):
    notes = generate_notes(
        [
            "feat: add WaveRunner",
            "fix: handle empty manifest",
            "ci: add workflow",
        ]
    )

    assert "### 🚀 Features" in notes
    assert "### 🐛 Bug Fixes" in notes
    assert "### 🧰 Maintenance & CI" in notes
