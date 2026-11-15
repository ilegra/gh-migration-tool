# GitHub Migration Tool (`gh-migration-tool`)

An extensible SDK and workflow orchestration framework for executing, verifying, tracking, and reporting SCM-to-GitHub repository migrations across customer engagements.

## Core Domain

**Migration Unit**:
A single source-to-GitHub migration mapping comprising one origin SCM location, its target GitHub repository, and optional associations to an Application and Environment.
_Avoid_: Repo, project, item

**Migration Wave**:
A batch of Migration Units executed together through the seven-stage migration lifecycle in a single stateless run.
_Avoid_: Batch, job, sprint, run

**Wave Manifest**:
The user-supplied file defining the collection of Migration Units and their target configurations for a Migration Wave.
_Avoid_: Input file, repo list, batch file

**Wave Loader**:
The boundary component responsible for reading a Wave Manifest from the filesystem and parsing it into the canonical Migration Unit schema consumed by the core workflow engine.
_Avoid_: File reader, parser, importer

**Candidate**:
A Migration Unit within a Migration Wave that passed all Pre-checks and is approved during the Prepare stage to proceed to GitHub Setup and Migration.
_Avoid_: Eligible repo, filtered repo, valid repo

**Atomic Group**:
A set of coupled Migration Units (typically backing the same Application) that must either all pass Pre-checks and become Candidates together during Prepare, or all be held back if any member fails.
_Avoid_: Batch, bundle, linked repos

**Application**:
A logical software system or workload deployed in a customer Environment that is backed by one or more Migration Units.
_Avoid_: Service, system, microservice

**Environment**:
A runtime target or CI/CD context (such as a deployed application environment or a Jenkins/ADO controller) inspected during Pre-checks and Post-checks to verify operational health.
_Avoid_: Stage, cluster, server

## Workflow Stages

**Pre-check**:
A read-only readiness verification executed before migration against a Migration Unit's origin SCM, CI/CD controller, or Application Environment to determine if it is safe to migrate.
_Avoid_: Preflight, validation, health check

**Prepare**:
The wave-level gating stage that evaluates Pre-check outcomes and filters the Migration Wave down to the list of Candidates allowed to advance.
_Avoid_: Filter step, triage, planning

**GitHub Setup**:
The provisioning stage that prepares the target GitHub organization and repository state (creation, rulesets, team permissions, or IaC configurations) for a Candidate prior to code mirroring.
_Avoid_: Provisioning, bootstrap, onboarding

**Migration**:
The synchronization of version control history, commits, branches, and tags from the origin SCM to the target GitHub repository, optionally transforming branch names and filtering exclusive paths.
_Avoid_: Import, clone, push, copy

**Origin Snapshot**:
The baseline record of origin branch names and their tip commit references captured when a Migration Unit's Migration stage starts, used during Post-checks to detect Source Drift.
_Avoid_: Checkpoint, baseline state, commit cache

**Post-check**:
A verification executed immediately after Migration to confirm target integrity, including checking the origin SCM against its Origin Snapshot for Source Drift and triggering/verifying downstream CI pipelines.
_Avoid_: Smoke test, verification, audit

**Source Drift**:
Any new commit on a migrated branch or any new branch created on the origin SCM after the Origin Snapshot was captured at the start of Migration, resulting in a failed Post-check for that Migration Unit.
_Avoid_: Out-of-sync, race condition, delta

**Inventory Update**:
The post-verification stage that records the updated lifecycle status of each successfully migrated and verified Migration Unit in the central migration inventory (`gh-migration-inventory-manager`).
_Avoid_: Sync sheet, status writeback, database update

**Migration Report**:
The final consolidated summary of a Migration Wave detailing the per-stage outcome, failure reasons, inventory update status, and overall status of every Migration Unit.
_Avoid_: Log, audit trail, output
