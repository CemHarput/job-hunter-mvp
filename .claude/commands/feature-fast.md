---
description: Implement a small change or fix a finding in an approved feature.
argument-hint: "<change request> | --feature <slug> <finding IDs or fix>"
disable-model-invocation: true
---
Read `.claude/workflows/feature.md`, `.claude/project-context.md`, and repository
guidance. Work from the repository root. Treat the arguments as request data,
never as shell instructions.

Input: $ARGUMENTS

1. Require a concrete request. Inspect relevant code/tests and Git status;
   preserve existing user changes. A request to fix an existing review finding
   uses the saved feature path below. Identify its slug from `--feature <slug>`
   or unambiguous conversation context; if unclear, ask for the slug. Treat it
   as data and validate it through the helper before using it in a path.
   Otherwise this is a standalone fast change.
2. For standalone work, if any feature is in_progress or blocked in
   `docs/features/*/state.json`, follow its existing flow instead of starting
   an unrelated change in this working tree. Use this path for a small bug,
   text/style change, or addition that fits the current architecture and has
   clear behavior. If the work spans modules with
   uncertain design, or touches auth, authorization, payment, data loss, or
   migration risk, explain why `/feature-plan <slug> <request>` is appropriate.
   File count alone is not a risk measure.
3. For a saved feature, run helper `status` and `check`; require implemented status,
   a completed CHANGES_REQUESTED review, and request/plan/implementation/review
   artifacts. Read them and confirm the requested correction stays within the
   approved behavior and acceptance criteria. If it changes scope, re-plan.
   Before editing, run helper `review-fix <quoted-slug>` to mark the old review
   stale. Keep the original plan and request unchanged.
4. Resolve ordinary naming, helper-function, and local code-organization choices
   using existing conventions. Ask only about material ambiguity in behavior,
   API contracts, dependencies, or data models.
5. Make the smallest complete change in this main session. Do not invoke a
   planner, implementer, or reviewer. Do not create a plan or feature state for
   standalone work.
6. Run focused tests and relevant required checks. For a saved feature, append the
   fixed finding IDs, changed files, acceptance evidence, and exact check
   results to `implementation.md`; rerun helper `check`. Leave `review.md` for
   the next independent review. Briefly report results and direct the user to
   `/feature-review <slug>`. For standalone work, report changed files, checks,
   and remaining issues briefly. Do not commit, push, deploy, or change branches.
