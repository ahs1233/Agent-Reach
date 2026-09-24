# Ahmed Toolbox Production Baseline

Date: 2026-09-24  
Repository: `ahs1233/Agent-Reach`  
Branch: `feat/ahmed-toolbox-mcp`  
Production SHA: `f6b9f05e2b1f78325c0b5d0223c5eb2f6c3d9eca`  
Railway deployment: `f393233a-ee05-49d0-a107-7b15cf8ed7ca`  
Public endpoint: `https://agent-reach-production.up.railway.app`

## Deployment state

- Railway deployment: SUCCESS
- MCP listener logged on `0.0.0.0:8080/mcp`
- persistent volume: `/root/.agent-reach`
- healthcheck path: `/health`
- production region: iad, one replica
- GitHub general `ci`: SUCCESS on exact SHA
- `Ahmed ToolBox CI`: SUCCESS on exact SHA

## Runtime verification observed in production

Dual-temporal startup acceptance:

- status: PASSED
- current live gate: PASSED
- stale-live gate: BLOCKED
- contradiction selected current data: true
- run audit: true
- live/recent retrieval method: `reach_read_url`

Startup skill state:

- skill: `__startup_doctor_skill__`
- revision: 1
- successes: 8
- failures: 0
- score: 0.90
- trust status: `EXPERIMENTAL`

This is correct under the stabilization trust policy because Trusted requires at least 10 qualifying successes. No synthetic executions were added merely to promote the skill.

## Resource baseline

One-hour Railway sample, 61 points:

| Measurement | Average | Min | Max |
| --- | ---: | ---: | ---: |
| CPU_USAGE | 0.002430 | 0 | 0.014990 |
| MEMORY_USAGE_GB | 0.321071 | 0.304484 | 0.376156 |
| NETWORK_RX_GB/sample | 0.000022783 | 0 | 0.000280968 |
| NETWORK_TX_GB/sample | 0.000006442 | 0 | 0.000034315 |

## Known production weakness

The live model-subagent startup acceptance is currently degraded:

- failure: `SubagentModelError: model provider deadline exceeded`
- observed delegation duration: 60422.75 ms
- core MCP service remained serving and Railway deployment remained SUCCESS

This is recorded as an unresolved external-provider reliability weakness. It is not hidden or counted as a pass.
