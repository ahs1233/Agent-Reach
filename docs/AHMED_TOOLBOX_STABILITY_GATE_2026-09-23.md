# Ahmed Toolbox — Stability-First Engineering Gate

**Status:** ACTIVE / MANDATORY  
**Effective date:** 2026-09-23  
**Scope:** Ahmed Toolbox, Agent-Reach integrations, Runtime, Orchestration, Memory, Browser Use, ACE, PanWatch integration, deployment/runtime changes.

## 1. Non-negotiable principle

No feature is successful if it increases capability while reducing system stability.

Engineering priority order:

**Stability → Compatibility → Safety → Capability → Performance**

A feature must not enter production merely because its happy path works.

## 2. Mandatory pre-merge gate

Every new feature, integration, runtime change, dependency change, or orchestration change must pass all applicable gates below before merge:

1. **Architecture impact**
   - Identify the components and control-plane/execution-plane boundaries touched.
   - Confirm the change does not create hidden coupling or duplicate an existing responsibility.

2. **Recursion / cycle safety**
   - Check for direct and indirect recursive tool calls.
   - Control-plane tools must not re-enter control-plane execution through worker/runtime paths unless an explicitly bounded mechanism exists.
   - Workflow DAGs must reject accidental cycles.

3. **Failure isolation**
   - Test tool failure, timeout, malformed output, partial dependency outage, unavailable external service, and cancellation.
   - A failing specialist/tool must not crash or deadlock the whole system.
   - Failures must return a bounded, observable state.

4. **Backward compatibility**
   - Existing public tool contracts, schemas, workflows, and critical behavior must remain valid unless a migration is explicitly approved.
   - Any incompatible change requires migration notes and rollback steps.

5. **Regression coverage**
   - Run focused tests for the modified component.
   - Run the Ahmed Toolbox regression suite.
   - Add a regression test for every discovered bug that could recur.
   - A known failing test may not be ignored without a documented reason.

6. **Resource and runaway protection**
   - Validate bounded retries, bounded recursion/delegation, timeouts, concurrency limits, and tool-call budgets where applicable.
   - Verify that a single request cannot create uncontrolled nested execution.

7. **Integration safety**
   - Verify interactions with Runtime, Orchestration, Memory, Skills, Browser Use, ACE, and PanWatch where relevant.
   - Test both the feature itself and the neighboring systems most likely to be affected.

8. **Staging / realistic smoke test**
   - Do not rely only on unit tests for deployable runtime changes.
   - Run a realistic smoke test in the target environment before treating the change as production-ready.

9. **Rollback readiness**
   - Every production-affecting change must have a clear rollback path.
   - The previous known-good state must be identifiable before deployment.
   - If rollback is not realistically possible, the change requires explicit review before production.

10. **Post-deploy verification**
    - A build starting or a deployment being queued is not success.
    - Observe the deployment reach a terminal healthy/success state.
    - Perform a real smoke test of the affected capability after deployment.
    - Check logs/health for regressions before declaring completion.

## 3. Stop conditions

The change MUST NOT be merged or promoted when any of the following is true:

- a critical regression test fails;
- recursive/control-plane behavior is unbounded or unclear;
- rollback is undefined for a production-affecting change;
- the feature breaks an existing critical contract;
- the system can deadlock, crash globally, or create uncontrolled tool-call growth under a tested failure mode;
- production health has not been verified after deployment.

## 4. Definition of Done for a feature

A feature is DONE only when:

- the intended behavior works;
- existing critical behavior still works;
- failure modes are bounded;
- regression tests exist and pass;
- integration points are tested;
- rollback is defined;
- deployment reaches terminal success/healthy state when applicable;
- a real post-deploy smoke test passes;
- evidence of the verification is recorded in the PR or handoff.

## 5. Design rule learned from Runtime hardening

Management/control layers and execution/worker layers must remain separated.

Preferred direction:

```text
User / Agent
    ↓
Orchestrator / Control Plane
    ↓
Runtime / Execution Plane
    ↓
Specialist / Worker Tool
    ↓
Result
```

Do not permit uncontrolled re-entry such as:

```text
Runtime → Orchestrator → Runtime → Orchestrator → ...
```

Any intentional re-entry must be explicit, bounded, tested, and observable.

## 6. Review rule

For every future feature, reviewers must answer two independent questions:

1. **Does the new feature work?**
2. **Can the new feature break, destabilize, recursively amplify, or silently corrupt anything that already works?**

A "yes" to the first question is insufficient without a satisfactory answer to the second.

## 7. Permanent project rule

This policy is a release gate, not optional guidance. New capability must be earned without sacrificing the known-good system.
