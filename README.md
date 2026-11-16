# GitHub Migration Tool (`gh-migration-tool`)

An extensible Python SDK and CLI for orchestrating 7-stage SCM-to-GitHub repository migrations across customer engagements.

## 7-Stage Migration Wave Lifecycle

1. **Pre-checks** (`PreCheck` interface): Readiness checks before migration (built-in: `JenkinsJobPreCheck`).
2. **Prepare** (Core): Filters Pre-check outcomes into `Candidates` and enforces `atomic_group` coupling.
3. **GitHub Setup** (`GitHubSetup` interface): Prepares target repositories, teams, and rulesets via GitHub App auth (`GitHubApiSetup`) or verifies IaC-managed repositories (`VerifyRepoExistsGitHubSetup`).
4. **Migration** (`Migrator` interface): Captures an `OriginSnapshot` and mirrors code using `gh-repo-sync` (`v1.1.1`) with `[skip actions]`.
5. **Post-checks** (`PostCheck` interface): Detects `Source Drift` (`SourceDriftPostCheck`) and triggers/polls CI (`JenkinsTriggerPostCheck`, `GitHubActionsTriggerPostCheck`).
6. **Inventory Update** (Core): Updates Google Sheets lifecycle status via `gh-migration-inventory-manager` (`v1.3.7`).
7. **Migration Report** (Core): Emits Markdown, JSON, and Console summaries.

## Development

```bash
poetry install --with dev
poetry run ruff check src tests
poetry run ruff format --check src tests
poetry run mypy src tests
poetry run bandit -r src
poetry run pytest tests/unit -v
```
