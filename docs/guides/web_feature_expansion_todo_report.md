# Web Feature Expansion TODO Report

Updated: 2026-02-18

Scope: Track delivery against the previously selected phased plan ("Full Feature Expansion Plan: Reliability-First, Phased Delivery"), while keeping Streamlit active.

Locked constraints:
- Streamlit remains active and supported.
- Changes are additive; no breaking removals.
- Reliability and typed failure handling come before new UX scope.

## Phase 0: Stability baseline and no-hidden-failure gate
Status: In progress

Implemented:
- Button/endpoint matrix exists (`docs/guides/web_button_matrix.md`).
- Web migration checklist exists (`docs/guides/web_migration_checklist.md`).
- Web smoke script exists (`tools/web_smoke.py`).
- Mutating route logging is present in API handlers with `request_id` support.

Remaining TODO:
- [ ] Close all known UI test failures before treating this phase as done (current `CacheInspectorTab` test collision on "Strict hit").
- [ ] Add explicit CI smoke gate for `tools/web_smoke.py`.
- [ ] Verify every visible action returns explicit success/error text in UI.

## Phase 1: Cache inspector and provenance scoring
Status: In progress (feature-complete, stabilization pending)

Implemented:
- Cache inspector service and routes:
  - `GET /api/v1/cache/chat/inspect`
  - `GET /api/v1/cache/structured/inspect`
- Provenance scoring service and route:
  - `POST /api/v1/chat/provenance-score`
- Web Cache Inspector tab and chat provenance badges are wired.
- Backend tests exist in `tests/test_web_api.py` and `tests/test_web_services.py`.

Remaining TODO:
- [ ] Resolve frontend Cache Inspector test ambiguity to restore full green frontend suite.
- [ ] Add additional negative-case tests for malformed/partial citation payloads.

## Phase 2: Async jobs for chat/structured generation
Status: Planned

Implemented prerequisites:
- Postgres async queue exists (`workflow.async_jobs`) in `ragonometrics/integrations/rq_queue.py`.
- Async queue migration already exists (`alembic/versions/0003_async_jobs.py`).

Remaining TODO:
- [ ] Add web job submission/status/cancel API routes under `/api/v1/jobs/*`.
- [ ] Add web jobs tracking table if separate from `workflow.async_jobs` is still required by product decision.
- [ ] Add frontend job progress UX with retry/cancel and result handoff.
- [ ] Add API and UI tests for long-running job flows.

## Phase 3: Project workspace isolation and RBAC
Status: Planned

Remaining TODO:
- [ ] Add project schema (`auth.projects`, memberships, paper mapping, roles).
- [ ] Add `/api/v1/projects*` routes and server-side role guards.
- [ ] Scope chat history, notes, workflow cache, usage, and structured flows by project.
- [ ] Add cross-project leakage tests.

## Phase 4: Evaluation harness (deterministic rubric and evidence checks)
Status: In progress (benchmarking present, regression harness pending)

Implemented:
- Web benchmark tooling exists (`ragonometrics/eval/web_cache_benchmark.py`).
- CLI wiring for benchmarks exists in `ragonometrics/cli/entrypoints.py`.

Remaining TODO:
- [ ] Add deterministic regression harness module (`ragonometrics/eval/regression_harness.py`).
- [ ] Add golden dataset schema (for example `bench/golden_questions.schema.json`).
- [ ] Add `ragonometrics eval-regression` CLI command and CI threshold gate.
- [ ] Emit machine + human readable reports (JSON + markdown).

## Phase 5: Cost and latency guardrails
Status: Planned

Remaining TODO:
- [ ] Add guardrail policy storage and service logic.
- [ ] Add `/api/v1/guardrails*` routes.
- [ ] Enforce token/timeout/budget guardrails at service boundaries.
- [ ] Surface guardrail events in Usage tab.

## Phase 6: Citation network and OpenAlex UX upgrade
Status: In progress

Implemented:
- Dynamic citation graph via `vis-network` is live.
- OpenAlex metadata cards and link-rich entities are live.

Remaining TODO:
- [ ] Add graph filters: year range, min citations, author/source filters.
- [ ] Add cluster coloring and synchronized table selection states.
- [ ] Add "open top evidence in viewer" hooks directly from graph context.
- [ ] Add performance guardrails for larger graphs.

## Phase 7: Collaboration features
Status: Planned

Implemented prerequisites:
- Notes/highlights support exists in current web app.

Remaining TODO:
- [ ] Add pinned Q&A table.
- [ ] Add structured-row comments/threads.
- [ ] Add shared annotation visibility toggles by scope/role.
- [ ] Add project activity feed and audit events.

## Phase 8: Release gates and rollout discipline
Status: In progress

Implemented:
- CI runs backend tests and frontend build/tests (`.github/workflows/ci.yml`).

Remaining TODO:
- [ ] Add phase-based feature flags for rollout safety.
- [ ] Add explicit web smoke + Streamlit smoke gates in CI.
- [ ] Add release checklist requiring zero 5xx across happy-path actions.
- [ ] Define two-release fallback rules for major new feature toggles.

## Next execution order
1. Finish Phase 0 stabilization gate and restore full green test baseline.
2. Complete remaining Phase 1 reliability/test items.
3. Deliver Phase 2 async jobs.
4. Deliver Phase 4 regression harness before major new feature surface area.
5. Continue with Phase 6 UX upgrades, then Phase 3/5/7 by risk and value.
