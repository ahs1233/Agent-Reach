# Ahmed Toolbox Stress Test Report

Date: 2026-09-24  
Repository: `ahs1233/Agent-Reach`  
Branch: `feat/ahmed-toolbox-mcp`  
Verified CI SHA: `f6b9f05e2b1f78325c0b5d0223c5eb2f6c3d9eca`

## CI gate

Ahmed ToolBox CI run 367 completed successfully on the verified SHA.

Full regression result:

- 853 passed
- 18 skipped
- 0 failed
- duration: 25.29 s
- Ahmed Toolbox coverage baseline: 72.86%

The 18 skipped cases are explicitly classified unavailable/not-present or live-prerequisite cases; they are not counted as passes.

## Deterministic core stress results

The `scripts/toolbox_stress.py` stress gate completed successfully.

| Scenario | Result | P50 | P95 | Max / wall |
| --- | --- | ---: | ---: | ---: |
| 10 concurrent local web retrievals | 10/10, 100% | 10.237 ms | 1028.981 ms | 1028.981 ms |
| 20 sequential ResearchRuns | 20/20, 100% | 0.942 ms | 1.143 ms | 2.848 ms |
| 50 MCP calls | 50/50, 100% | 7.151 ms | 10.277 ms | wall 0.04 s |
| 1000 EvidenceItems + audit | 1000/1000, 100%; audit passed | 0.226 ms | 0.521 ms | 3.237 ms |
| 10 parallel DAG branches | 10/10 steps, 100% | 6.603 ms | 6.603 ms | workflow 4.58 ms |

Process measurements:

- CPU time: 0.264 s
- peak RSS: 40.715 MB

Explicit skips:

- 5 media ingestions: skipped because live STT/media prerequisites are intentionally absent from deterministic CI.
- subagent saturation: skipped because deterministic CI does not use the external model credential; bounded runtime limits are covered separately.

## Interpretation

The deterministic core stress gate is PASS. This does not convert live external dependencies into passes and does not by itself satisfy the Production Acceptance gate.
