## Summary

Describe the change and why it is needed.

## Stability Gate — mandatory

- [ ] I identified the affected architecture boundaries and neighboring components.
- [ ] I checked for direct/indirect recursion, control-plane re-entry, workflow cycles, and runaway delegation.
- [ ] I tested failure paths: timeout, dependency/tool failure, malformed/partial output, and cancellation where applicable.
- [ ] Retries, recursion, concurrency, delegation, and tool-call growth are bounded where applicable.
- [ ] Existing public contracts / schemas / critical workflows remain compatible, or migration is documented.
- [ ] Focused tests for the changed component pass.
- [ ] Ahmed Toolbox regression tests pass.
- [ ] A regression test was added for any bug fixed by this PR.
- [ ] Integration with Runtime / Orchestration / Memory / Skills / Browser Use / ACE / PanWatch was checked where relevant.
- [ ] A realistic smoke test was run for runtime/deployment-affecting changes.
- [ ] Rollback path and previous known-good state are identified for production-affecting changes.
- [ ] Deployment reached a terminal healthy/success state before being called successful.
- [ ] Post-deploy smoke test and health/log verification passed where applicable.

If any item is not applicable, explain why below rather than silently skipping it.

## Evidence

Tests run:

```text
<commands / workflow links / results>
```

Runtime or staging smoke test:

```text
<result>
```

Rollback:

```text
<previous known-good commit/deployment and rollback method>
```

## Stability rule

No feature is considered complete if it increases capability while reducing system stability.

See: `docs/AHMED_TOOLBOX_STABILITY_GATE_2026-09-23.md`
