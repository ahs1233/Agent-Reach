# Ahmed Toolbox Architecture Diagrams

> هذه الرسومات هي المصدر المرجعي Mermaid للخرائط المستخدمة في وثيقة Ahmed Toolbox. تم اشتقاقها من البنية التي تم التحقق منها في `ahs1233/Agent-Reach` على الفرع `feat/ahmed-toolbox-mcp` عند HEAD `14d42a09a1a47607e2224f11e026a694d6aa7344`، مع ملاحظة أن حالة GitHub قد تتغير بعد إنشاء هذا الملف.

## D1 - High-Level Architecture

```mermaid
flowchart TB
  A[Applications / Consumers] --> B[MCP Interface + Auth Boundary]
  B --> C[Ahmed Runtime / Orchestration]
  C --> D[Memory + Sessions + Skills]
  C --> E[Research Engine]
  C --> F[Delegation / Model Subagents]
  E --> G[Evidence + Claims + Provenance]
  E --> H[Dual-Temporal Intelligence]
  C --> I[Agent-Reach Acquisition]
  I --> J[Web / Jina / Search]
  I --> K[Browser Use + Chromium/CDP]
  I --> L[YouTube / yt-dlp / Browser Inspection]
  I --> M[Remote MCPs: Scrapling allowlist]
  C --> N[ACE Domain Layer]
  O[(Durable SQLite / Railway Volume)] --- C
  P[Observability / Doctor / Acceptance] --- C
```

## D2 - Master Mental Map

```mermaid
mindmap
  root((Ahmed Toolbox))
    Interface
      MCP
      Auth
      Tool discovery
    Runtime
      DAG workflows
      Delegation
      Sessions
      Status
    Orchestration
      Specialists
      Effect classes
      Budgets
      Hash-linked journal
    Research
      Sources
      Evidence
      Claims
      Provenance
      Audits
      Source independence
      Freshness
      Dual-temporal
    Acquisition
      Web search
      URL read
      Fallback retrieval
      Browser Use
      YouTube
      Remote MCP Scrapling
    Memory
      Runtime memory
      Session history
      FTS5
    Skills
      Versioning
      Bayesian score
      Candidates
      Rollback
    Domain Systems
      ACE
      PanWatch
      GTG / Gen1
      Future apps
    Reliability
      CI
      Startup acceptance
      Health
      Doctor
      Observability
```

## D3 - Request Execution Flow

```mermaid
flowchart TD
  U[User / Application Request] --> I[Intent + Contract]
  I --> S{Existing session/memory useful?}
  S -->|yes| M[Retrieve bounded context]
  S -->|no| K{Reusable skill?}
  M --> K
  K -->|yes| X[Execute versioned skill]
  K -->|no| P[Build workflow / delegate / direct tool plan]
  P --> T[Authorize tools + budgets + effect class]
  X --> T
  T --> A[Acquisition / computation]
  A --> E[Evidence + provenance]
  E --> V[Freshness / independence / temporal checks]
  V --> Y[Synthesis / output audit]
  Y --> R[Result]
  R --> L{Reusable learning?}
  L -->|yes| W[Memory / skill candidate / outcome]
  L -->|no| Z[End]
  W --> Z
```

## D4 - Research Flow

```mermaid
flowchart LR
  Q[Question] --> R[Research Run]
  R --> S[Source discovery]
  S --> A[Acquisition events]
  A --> E[Evidence items]
  E --> C[Claims]
  C --> P[Provenance links]
  P --> F[Freshness + source independence]
  F --> T[Temporal validity / fusion]
  T --> O[Output fragments]
  O --> AU[Output audit + run audit]
  AU --> X[Exportable ledger / synthesis]
```

## D5 - Authentication Flow

```mermaid
sequenceDiagram
  participant C as Consumer (PanWatch / client)
  participant S as Secret Store / Railway Variables
  participant A as /auth/token
  participant M as /mcp
  C->>S: Load durable refresh credential at process start
  C->>A: POST Bearer refresh credential
  A-->>C: Short-lived HMAC access token + expiry
  C->>M: MCP request with access token
  M-->>C: Result
  Note over C,M: On expiry or HTTP 401
  C->>A: Refresh again
  A-->>C: New access token
  C->>M: Retry once
  Note over C,M: After server/client restart no RAM session is required
```

## D6 - Failure & Recovery Flow

```mermaid
flowchart TD
  P[Primary backend] -->|success| OK[Validated result]
  P -->|blocked / rate limit / weak content| F1[Fallback backend]
  F1 -->|success| EV[Record retrieval history + provenance]
  F1 -->|failure| F2[Stealth / browser fallback]
  F2 -->|success| EV
  F2 -->|failure| ALT[Targeted alternative discovery]
  ALT --> CONF[Assess evidence confidence / coverage]
  EV --> CONF
  CONF -->|sufficient| OK
  CONF -->|partial| DEG[Return degraded/partial with explicit limitations]
  CONF -->|unsafe or unauthorized| DENY[Fail closed]
```

## D7 - Memory & Skill Flow

```mermaid
flowchart LR
  R[Successful complex workflow] --> C[Skill candidate]
  C -->|threshold met| S[Save skill revision]
  S --> T[Test / execute]
  T --> O[Outcome record]
  O --> B[Bayesian score + trust policy]
  B -->|healthy| RE[Reuse]
  B -->|regression| RB[Rollback -> new monotonic revision]
  M[(Runtime Memory)] --> RE
  SS[(Session Store)] --> RE
  RE --> O
```

## D8 - Orchestration / Delegation Safety

```mermaid
flowchart TB
  O[Parent orchestration] --> B[Budget + max effect class]
  B --> D[Delegate task]
  D --> C[Child orchestration ID]
  C --> A[Role allowlist + tool allowlist]
  A --> T[Tool authorization]
  T --> J[Hash-linked event journal]
  J --> R[Bounded child result]
  R --> O
  X[Recursive runtime_/orchestration_ nested workflow call] -->|denied| DENY[Control-plane recursion guard]
```

## D9 - PanWatch Integration

```mermaid
flowchart LR
  P[PanWatch] --> C[AhmedToolboxClient]
  C --> R[Durable refresh credential]
  R --> A[/auth/token]
  A --> AT[Short-lived access token]
  AT --> M[/mcp]
  M --> RT[Ahmed Runtime / Reach / Research]
  RT --> E[Evidence / context]
  E --> P
  C -->|401| RR[Invalidate access + refresh + retry]
  C -->|transport error| TR[Replace HTTP client + retry]
  RR --> M
  TR --> M
```

## D10 - ACE Integration

```mermaid
flowchart TD
  C[Campaign / objective] --> R[ACE Research]
  R --> AR[Ahmed Research + Reach]
  AR --> E[Evidence-backed findings]
  E --> H[Hypotheses]
  H --> X[Experiments / variants]
  X --> G[Generate scripts/assets/packages]
  G --> M[Record metrics]
  M --> V[Evaluate with uncertainty]
  V --> L[Learning]
  L --> N[Next experiment]
  N --> H
```

## D11 - Ecosystem

```mermaid
flowchart TB
  AT((Ahmed Toolbox))
  PW[PanWatch] --> AT
  GTG[GTG / Gen1 Gold] --> PW
  ACE[ACE] --> AT
  AS[AlSouq Intelligence] -. future .-> AT
  BI[Business Intelligence] -. future .-> AT
  CR[Content / Research Agents] -. future .-> AT
  MON[Monitoring Systems] -. future .-> AT
  TA[Trading Research] -. future .-> AT
  AT --> WEB[External Web / APIs / MCPs]
```

## D12 - Production Reliability Loop

```mermaid
flowchart LR
  G[Git commit] --> CI[Focused + regression CI]
  CI -->|pass| DEP[Railway deploy]
  DEP --> HC[/health]
  HC --> BR[Chromium/CDP smoke]
  BR --> RA[Runtime startup acceptance]
  RA --> DT[Dual-temporal acceptance]
  DT --> LIVE[Live-network acceptance]
  LIVE --> OBS[Logs / execution stats / doctor]
  OBS --> FIX[Regression fix]
  FIX --> G
```