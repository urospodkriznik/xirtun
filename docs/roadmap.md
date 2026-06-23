# Roadmap

> v1 scope is **LOCKED**. New ideas go to the v2 candidate list below — they do
> not reopen v1. See [product.md](product.md) for scope definitions and
> [decisions.md](decisions.md) for why things are where they are.

## v1 — LOCKED

The smallest thing that is genuinely useful and exercises real autonomy.

1. **Meal logging** via Telegram text, with a cheap-LLM clarification loop.
2. **Approximate nutrition estimation** (LLM-only, ±20–30%).
3. **`diet.md`** — agent-managed profile with onboarding interview + merge +
   version snapshots.
4. **Symptom logging** through the same intake pipeline.
5. **Weekly autonomous summary** — real agent loop, reads recent window +
   `observations.md`, sends a proactive Telegram message, updates its memory.
6. Supporting: `Messenger` abstraction (Telegram long-poll), `LLMClient`
   abstraction (Gemini Flash-Lite + Pro), SQLite storage, APScheduler trigger,
   edit/undo-last-entry, export, Docker + GCP VM deploy, the test suite from
   ADR-011.

**Definition of done for v1:** the success criteria in [product.md](product.md).

## v2 — candidate list (NOT committed; not ordered)

Captured so good ideas are not lost. Each needs its own scoping before it starts.

### Input & interaction
- **Voice input** (explicitly planned by the author for v2).
- **Photo input** (vision model to identify food from a picture).
- **Agent-planted follow-up questions:** the weekly run writes targeted questions
  into `observations.md` (e.g. "does this burger contain soy?") that the hot path
  asks at logging time, to test a hypothesis.

### Accuracy & data
- **Food database hybrid** (USDA FDC / OpenFoodFacts) for users who later want
  real calorie precision. `raw_text` is retained in v1 specifically to enable
  this without data loss.
- **Controlled tag vocabulary + `json_each` querying** — promote free-form tags
  to a reliable, queryable allergen/nutrient taxonomy.

### Analysis
- **Deterministic stats / commands:** `/today`, `/week` computing exact totals
  via SQL aggregation; charts.
- **Deterministic / statistical correlation** between tags and symptoms (beyond
  the LLM eyeballing a window), once there is enough data density to be honest.

### Active guidance & research
The longer-term direction: move from a *reference* that observes to an *active coach*
that guides diet change.
- **Proactive diet coaching:** concrete daily/weekly targets and substitutions, then
  follow-ups on whether suggestions were adopted, adjusting over time.
- **Shopping-list assistant (hot path):** ask in the moment — "I'm heading to the
  shop, what should I add to my list?" — and get suggestions drawn from your goals,
  recent diet, and observed gaps.
- **Health-metric tracking:** sleep, blood pressure, weight trend, hydration — the
  weekly run requests these (it can already ask questions) and then correlates them.
- **Two-way weekly questions:** capture answers to agent-asked questions as
  structured data linked to the relevant day/symptom, not just free-form notes.
- **Article / evidence lookup:** let the agent search reputable sources to support a
  suggestion (e.g. nutrients for a stated goal) and cite them. (Likely v3.)
- **Metric-based targets:** use age / sex / height / weight / activity to compute
  calorie and protein targets instead of reasoning about them qualitatively.

### Platform & ops
- **Webhook transport** (if real-time or scale ever matters).
- **Live multi-provider switching** / fallback between LLM providers.
- **Split scheduler to system cron** if process-coupling ever becomes a problem.
- **Multi-user** (would require auth, per-user isolation — a large change).

### Tracking depth
- **Per-session exercise/activity logging** with energy-balance math (calories
  burned, protein needs). v1 captures exercise/goals only as profile *context*
  via the `note` intent, which the weekly run reasons about qualitatively.
- Richer supplement tracking, mood, hydration, sleep, meal location/context.

## Explicitly parked

- Anything that turns this into a "professional accurate tracker." The product is
  a pattern-finding *helper* (see product.md non-goals).
- Diagnosis. The agent suggests *investigating* with a clinician; it never
  diagnoses.
