# Phase 0 Spec: Contracts for a Gradial-class platform

Status: v0.1, 2026-11-12. The contracts in `src/gradial_contracts/` are the source of truth; this file explains them.
What is verified: 28 tests pass against an in-memory reference adapter, and three deliberate breakages of that adapter were each caught.
What is NOT verified: any real CMS, any LLM behaviour, any deployment. Estimates from the earlier plan (6 weeks, $15-40/month) are still unvalidated.

## The six contracts

| # | Contract | Module | Producer -> consumer |
|---|---|---|---|
| 1 | Task | `task.py` | Intake -> orchestrator |
| 2 | Canonical page model + CMS adapter | `page_model.py`, `adapter.py` | Agents/tool gateway <-> CMS connectors |
| 3 | Run state and step graph | `run.py` | Orchestrator <-> workers |
| 4 | Context formats | `context.py` | Admin/ingestion -> agents and governance |
| 5 | Governance report and autonomy policy | `governance.py` | QA agent -> review/publish |
| 6 | Approval token and audit log | `audit.py` | Review UI -> adapter; everything -> audit |

## Invariants (each is enforced by a test)

- I1 Task status moves only along `ALLOWED`; DONE and CANCELLED are final; FAILED re-queues only explicitly.
- I2 A step graph with a cycle, unknown dependency or duplicate id cannot be constructed.
- I3 A permanent failure skips only that item's downstream steps; other items continue; outcome is `partial`.
- I4 A failed step with attempts left is runnable again, and the run is not complete.
- I5 Drafts never change live content; only `publish` does.
- I6 `publish` verifies the approval token before any write. The token is bound to task, draft, reviewer, expiry and the draft's content hash. Editing the draft after approval invalidates it.
- I7 `publish` fails with VERSION_CONFLICT if the live page moved after the draft's base version (human edit mid-run). The task returns to AWAITING_REVIEW.
- I8 `create_draft` and `publish` are idempotent per idempotency key; reusing a publish key for another draft is rejected.
- I9 Adapter operations outside the configured path scope, including listing a wider prefix, fail with PERMISSION_DENIED.
- I10 Only RATE_LIMITED and TRANSIENT are retried; everything else fails fast.
- I11 `requires_human` fails closed: a stale report (content changed after checks) or any failed blocker needs a person in every autonomy mode, including AUTO.
- I12 The audit log is a hash chain; edited or deleted events are detected.

## Decisions made while writing the contracts

- Pages are addressed by `(cms, path)`; components by stable `id`. Diffs and edits address components, not text offsets.
- The version token is opaque and compared for equality only, so it can be an ETag, a revision number or a content hash in different CMSs.
- A governance report is bound to a content hash, so it cannot be reused after an edit.
- Runs record pinned rule-pack versions, golden-page versions and exact model ids for reproducibility.
- Task `instruction` text is untrusted data. Tool authority comes only from the task type, adapter scope and approval token, never from text in a ticket or page.

## Open decisions (not settled by this phase)

1. **Stand-in CMS.** Strapi, Payload or WordPress is not chosen. Run `tests/adapter_conformance.py` against each candidate's adapter; pick the one that passes with the least workaround. Specific things to test: does it have real draft versus published state, a version/ETag that changes on every write, and an API that can enforce a path scope.
2. **Canonical page model coverage.** The model has components, props, children, template and locale. It has not been tested against a CMS with deep templates, content fragments or personalization. Expect it to change when the first real connector is written.
3. **Model routing.** Which model handles router, author, tagger and judge is undecided; `RunPins.models` only records the choice.
4. **Ticket write-back mapping.** Jira and GitHub Issues status names and transitions are not mapped to `TaskStatus`.
5. **Persistence schema.** These contracts need SQL tables (tasks, runs, steps, audit, rule packs, golden pages, assets). Audit storage must forbid UPDATE and DELETE; the hash chain only detects tampering.
6. **Review UI and the Rules & Skills editor** are not designed.
7. **Token key management.** The HMAC secret is a test constant here. A real deployment needs rotation and per-tenant keys.
8. **Rendered preview.** `preview_url` is in the interface, but nothing yet produces screenshots or visual diffs.

## How this closes items in the gap register

| Register item | Effect of Phase 0 |
|---|---|
| CMS fidelity | Partly: interface and conformance suite exist; real CMS not tested |
| Credentials and access | Partly: path scope and approval token designed; vault, RBAC, SSO not |
| Durable execution | Partly: per-item state, retries, resume semantics defined; no worker or queue |
| Integration plumbing | Partly: retry rules and idempotency defined; webhooks and pagination of Jira not |
| Model reliability | Partly: model pins recorded; no fallback or eval yet |
| Compliance controls | Partly: audit chain defined; storage and retention not |
| Evaluation harness, Rendered preview, Context ingestion, Admin UX, Tenant operations, Delivery basics, Browser fallback, Scope, Legal x3 | Not addressed |

## Next step after Phase 0

Phase 1 vertical slice: wire one task type (update copy) through these contracts using the reference adapter first, then the chosen stand-in CMS. The conformance suite is the gate for the CMS adapter.
