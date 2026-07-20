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
- **Photo input** (vision model to identify food from a picture) — same transport
  pattern as voice: download the image, describe/structure it, emit a normal
  `IncomingMessage`.
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
  via SQL aggregation; charts. *(Partly done: `get_intake_summary` now gives the
  weekly agent SQL-computed per-day totals + a week-over-week comparison.)*
- **Deterministic / statistical correlation** between tags and symptoms (beyond
  the LLM eyeballing a window), once there is enough data density to be honest.
- **Longitudinal / monthly trend analysis (build once there's 2–3 months of real
  data — ~Sep 2026 at current pace; do NOT build against empty history).** The
  weekly review stays focused on the last ~4 weeks; long-range comparison is a
  *separate feature* on a monthly/quarterly cadence, not crammed into every weekly
  run (the month barely moves week to week, and it would bloat cost + repeat itself).
  Design constraints that make month-vs-month *relevant* rather than noise:
  - **Deterministic, persisted rollups.** A `monthly_stats` table (avg
    kcal/protein/fibre, weight, symptom counts, top foods per month), computed in
    SQL. Never have the LLM eyeball a year of diary — that's garbage arithmetic and
    blown context. Month-over-month is then real SQL, not the model half-remembering.
  - **Like-for-like, or it lies.** Comparing an injured/low-activity month to a
    healthy one screams "you're eating way less!" — true but meaningless. Tag each
    period with context (injury/illness, activity level, eventually season); this is
    where the deferred *active-conditions* state (below) finally earns its place.
  - **Completeness-gated.** Only compare months that are adequately logged; surface
    the completeness rate rather than comparing a sparse month to a full one.
  - **Sustained-change, not blips.** Call something a trend only when it persists
    across several periods and clears noise — narrate the confound, don't just diff.
  - **Endpoint (≈1yr+):** same-month-last-year comparison once seasonality (holidays,
    summer vs winter) is real. Genuinely future.
- **Active-conditions state (prerequisite for honest longitudinal comparison, also
  useful now):** a stored injury/illness flag with set/cleared dates, settable by an
  agent tool, that (a) lets calibration lower the activity multiplier without
  re-inferring from meal notes, (b) raises the protein floor during immobility, and
  (c) auto-reminds to clear it so the multiplier doesn't stay low after recovery.
  Same guardrails as calibrated targets: clamps in code, mandatory rationale, missing
  data treated as "not recorded" never a measured zero. Deferred until the
  calibration loop has run a few real weeks in production.

### Active guidance & research
The longer-term direction: move from a *reference* that observes to an *active coach*
that guides diet change.
- **Proactive diet coaching:** concrete daily/weekly targets and substitutions, then
  follow-ups on whether suggestions were adopted, adjusting over time.
- **Health-metric tracking:** sleep, blood pressure, weight trend, hydration — the
  weekly run requests these (it can already ask questions) and then correlates them.
- **Two-way weekly questions:** capture answers to agent-asked questions as
  structured data linked to the relevant day/symptom, not just free-form notes.
- **Article / evidence lookup:** let the agent search reputable sources to support a
  suggestion (e.g. nutrients for a stated goal) and cite them. (Likely v3.)
- **Goal-adjusted targets:** the deterministic maintenance target exists; refine it
  with explicit surplus/deficit for the user's stated goal (gain/lose).
- **"What to eat today" recipe suggester,** two parts:
  1. Looks at the last week's logged meals to spot what's been missing and
     proposes something today that fills the gap. Optionally the user can list
     ingredients they need to use up before they spoil, and suggestions should
     work around those.
  2. Learned food preferences: track thumbs-up / thumbs-down / neutral per
     recipe or meal so it stops re-suggesting things the user dislikes.
     Conversation shape: agent proposes a recipe; "good, I'll do it" →
     thumbs-up (save); "I don't like it" → thumbs-down (save); "I don't have
     lentils right now" → neutral (no save), and it generates another
     suggestion. The back-and-forth continues until the user confirms/stops,
     or a 30-minute cooldown elapses.

### Deployment & CI/CD (planned — build once it's in daily use)
Goal: push to `main` → tests run → the VM updates and the service restarts. Mirrors
the SSH-deploy pattern used in the author's other repos (openclaw, myapp-devops);
crib the exact workflow from there when implementing.

**CI** (every push / PR):
- GitHub Actions: checkout → install `uv` → `uv sync` → `uv run ruff check` →
  `uv run pytest`. (Fully offline — no API keys needed, since tests use fakes.)

**CD** (on push to `main`, after CI passes):
- SSH into the VM and run a deploy script:
  `git pull --ff-only` → `uv sync` → `sudo systemctl restart xirtun`.
- Secrets in repo settings: `SSH_HOST`, `SSH_USER`, `SSH_KEY` (deploy key),
  optional `SSH_PORT`. Use e.g. `appleboy/ssh-action`.
- VM prerequisites (from Slice 9): a repo checkout, the `xirtun` systemd unit, and a
  deploy user with `NOPASSWD` sudo scoped to `systemctl restart xirtun`.

**Decisions to make then:**
- Push-based SSH (lighter) vs a self-hosted runner on the VM (one more process to
  maintain). Lean SSH for one small VM.
- Schema changes: today it's `CREATE TABLE IF NOT EXISTS` only; anything beyond
  additive needs a real migration step in the deploy.
- `.env` lives only on the VM and is never touched by CD. A brief restart is fine
  (single user, no zero-downtime requirement).

### Platform & ops
- **Webhook transport** (if real-time or scale ever matters).
- **Live multi-provider switching** / fallback between LLM providers.
- **Split scheduler to system cron** if process-coupling ever becomes a problem.
- **Multi-user** (would require auth, per-user isolation — a large change).

### Tracking depth
- **Energy-balance accounting** — exercise sessions are now logged with an estimated
  calorie burn; a future step is netting intake vs. expenditure into the targets.
- Richer supplement tracking, mood, hydration, sleep, meal location/context.

## Explicitly parked

- Anything that turns this into a "professional accurate tracker." The product is
  a pattern-finding *helper* (see product.md non-goals).
- Diagnosis. The agent suggests *investigating* with a clinician; it never
  diagnoses.
