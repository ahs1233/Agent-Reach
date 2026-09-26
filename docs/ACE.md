# Ahmed Content Experiment Engine — ACE

ACE is the content experimentation domain layer inside Ahmed Toolbox.

## Core rule

Evidence -> Hypothesis -> Experiment -> Measurement -> Learning.

ACE does not publish through unofficial hacks, invent analytics, copy one viral
video blindly, or force a winner when the sample is insufficient.

## Architecture reuse

ACE intentionally reuses the existing Ahmed Toolbox stack:

- Agent Reach and Browser Use for discovery and retrieval.
- Ahmed Research Engine for Source -> Evidence -> Claim provenance.
- Ahmed Runtime and Orchestration for bounded execution and durable workflows.
- Existing remote MCP retrieval providers such as Scrapling through the gateway.

ACE owns only domain state:

Campaign, ResearchFinding, Persona, Hypothesis, Experiment, Variant, Asset,
Publication, MetricSnapshot, Evaluation, Learning, and CostRecord.

This keeps research provenance in one place instead of creating a second evidence
database.

## MCP/API workflow

- `ace_status`
- `ace_create_campaign`
- `ace_research`
- `ace_generate`
- `ace_record_metrics`
- `ace_evaluate`
- `ace_learn`
- `ace_next`
- `ace_report`
- `ace_case_study`

This API is the CLI-equivalent for the current Ahmed Toolbox architecture.

## Research and content intelligence

`ace_research` uses real `reach_web_search`, `reach_retrieve_url`, and
`reach_youtube_browser_inspect` where appropriate. Retrieved evidence is
recorded in the existing Research Engine when it is enabled.

ACE separates observations from causal conclusions. Pattern output includes
support count, sample size, frequency, confidence, and
`causation_claimed=false`.

The deterministic MVP extracts signals such as:

- question hook
- problem/solution
- demonstration
- price comparison
- social proof
- CTA
- local-language context

Provider/model-assisted semantic extractors can be added later without changing
the ACE data model.

## Hypotheses and experiments

A hypothesis stores:

- target audience
- one main variable
- control
- variant
- expected outcome
- primary metric
- secondary metrics
- minimum evidence
- confidence
- evidence references
- status

Initial experiments attempt to change one main variable while holding the rest
stable. Both control and variant keep complete structured scripts:

Hook -> Setup -> Value -> Pattern Interrupt -> Proof/Demonstration -> CTA.

## Personas

Supported persona types are:

- Brand Account
- Founder Account
- Faceless Account
- AI Character
- Educational Persona
- Entertainment Persona
- Product-focused Account

AI characters must be transparent. ACE rejects an opaque AI character rather
than using it as fabricated human authority.

## Creative provider layer

The `CreativeProvider` contract exposes capabilities plus:

- estimated cost
- quality
- latency
- quota
- paid/free state
- availability

The built-in `local_manifest` provider is zero-cost and produces scripts,
captions, storyboards, thumbnail specifications, and publishing packages. It
does not pretend that an image, video, or voice file exists.

Real image/video/voice providers can advertise those capabilities through the
same interface, including local/open-source, ComfyUI, cloud video, and TTS
providers.

## FREE_ONLY and cost routing

FREE_ONLY defaults to true. Routing order is cost-aware and excludes paid
providers entirely in FREE_ONLY mode. If a free provider is unavailable or its
quota is exhausted and no other free provider exists, ACE fails explicitly.

CostRecord persists estimated/actual cost per provider operation.

## Publishing

Instagram, TikTok, and YouTube Shorts use a provider-neutral Publishing Package:

- experiment ID
- variant ID
- platform
- structured script
- asset/storyboard reference
- caption
- hashtags
- thumbnail specification
- matched-time testing guidance

Actual publishing must use an official configured API or remain manual.
Without publishing permission the status is `PACKAGE_READY`, never
`PUBLISHED`.

## Analytics and evaluation

ACE can store:

Views, Reach, Watch time, Average view duration, Completion rate, Likes,
Comments, Shares, Saves, Profile visits, Followers gained, Link clicks,
Conversions, and Revenue.

The normalized decision model combines:

- Retention
- Engagement
- Intent
- Conversion

Weights follow the campaign objective. The decision engine also considers
sample size, account scale, time since publication, missing data, and a
conservative uncertainty margin.

Possible result states:

- WINNER
- LOSER (per variant)
- INCONCLUSIVE
- NEEDS_MORE_DATA

KILL / ITERATE / SCALE uses evidence confidence and a campaign historical
baseline when one exists. A static threshold is not used by itself.

## Safety and truthfulness

Guardrails reject common forms of:

- fake testimonials
- fake credentials
- fabricated expertise
- impersonation
- guaranteed financial/medical outcomes
- opaque AI characters

Research/provider/API failure is preserved as failure. ACE does not substitute
mock metrics or invented social performance.

## Honest end-to-end case study

Call `ace_case_study` with a real goal such as:

`Market the Al-Souq shopping app in Iraq on Instagram and TikTok.`

The case study performs live public research through Ahmed Toolbox, then builds
5 hypotheses, 3 personas, 10 content concepts, selected initial experiments,
structured scripts, publishing packages, and a measurement plan.

Because no post-publication platform analytics exist at planning time, the
case study ends in `NEEDS_MORE_DATA` instead of fabricating a winning video.
