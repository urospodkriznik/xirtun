# Architecture Decision Log

> Append-only record of *why* the architecture is the way it is. Each entry:
> context, options considered, decision, rationale. Newer decisions may supersede
> older ones — mark superseded entries rather than deleting them.

Format: **ADR-NNN — Title** · Status: Accepted | Superseded

---

## ADR-001 — Raw Python, no agent framework · Accepted

**Context.** The author wants to own the agent loop and learn Python; explicitly
rules out LangChain, LlamaIndex, and similar.

**Decision.** Hand-written agent loop and tool dispatch. Allowed dependencies are
*libraries* (httpx, APScheduler, provider SDKs or raw HTTP, pytest), not
*frameworks* that own control flow.

**Rationale.** Ownership of the loop is a hard requirement and the primary
learning goal. Frameworks would hide exactly the part the author wants to
understand. Cost of writing the loop by hand is low (it is ~a `while` loop with a
dict-based dispatcher).

---

## ADR-002 — SQLite for the diary, tags as a JSON column · Accepted

**Context.** Food diary needs to support edits, the weekly LLM read, and a
future "find patterns over time" use case. Options: dynamic-key nested JSON,
flat JSONL, SQLite, vector DB.

**Decision.** SQLite (stdlib `sqlite3`, no ORM). Free-form tags stored as a JSON
array string column.

**Rationale.**
- Dynamic-key nested JSON is the *worst* shape for the stated soy-allergy query
  ("every meal containing soy") and was rejected outright.
- The usual reason to prefer JSONL over SQLite for a beginner — avoid learning
  SQL — does not apply: the author already knows SQL and `ALTER TABLE`. So SQLite
  costs ~nothing and spares a JSONL→SQLite migration when v2 adds deterministic
  stats.
- JSON tag column keeps v1 free-form (flexibility the author wanted: `iron-rich`,
  etc.) while leaving `json_each` querying available in v2 with no migration.
- Vector DB rejected: there is no semantic-search requirement; the weekly run
  reads a bounded recent window as text.

**Honest caveat (on record).** In v1 the correlation feature does **not** depend
on the store being a database — the LLM reads serialized text and finds patterns.
SQLite is justified by *edits + v2 stats + existing SQL fluency*, not by v1
correlation. If those weren't true, JSONL would have won.

---

## ADR-003 — LLM-only nutrition estimation, no food database in v1 · Accepted

**Context.** Options: pure LLM estimation, LLM + structured food DB (USDA FDC,
OpenFoodFacts), hybrid.

**Decision.** Pure LLM estimation. ±20–30% accepted.

**Rationale.** The goal is rough trend awareness and symptom causation, not
hitting calorie/macro targets. A food DB adds integration, lookup latency, and
matching logic for accuracy the author explicitly does not need. Numbers are
stored as estimates; `raw_text` is always retained so a food-DB hybrid can be
added in v2 without losing data.

---

## ADR-004 — Two loops: hot-path state machine, weekly agentic loop · Accepted

**Context.** "What earns the word agent here?" The mandatory autonomous behavior
is the weekly run.

**Decision.** Hot path (meal/symptom logging) is a constrained state machine
driven by user replies. The weekly run is a real tool-using agent loop that
autonomously decides which tools to call, what to conclude, and whether to
message.

**Rationale.** Meal logging is well-defined; making it a free-form tool loop
would waste tokens, add latency, and reduce predictability. Autonomy belongs in
the weekly run, where the model genuinely chooses actions. A scheduled trigger
does not make the weekly behavior non-autonomous — the autonomy is in the loop.

---

## ADR-005 — Two-tier models behind a provider abstraction · Accepted

**Context.** Cheap model needed for the hot path (Anthropic has no
ultra-cheap tier); strong big-context model for the weekly run. Author wants to
swap providers later (Gemini, OpenAI mini/nano, Groq).

**Decision.** An `LLMClient` interface with `complete(messages, schema?, tools?)`.
Start with Gemini Flash-Lite (hot) + Gemini Pro (weekly). Each provider is an
adapter class.

**Rationale.** The hard, provider-specific part is structured output and tool
calling; the abstraction exists to hide exactly that. The same seam enables
`FakeLLM` for tests. Accepting more upfront work than the messaging abstraction
because the cost ceiling and provider flexibility justify it.

---

## ADR-006 — APScheduler in-process for the weekly trigger · Accepted

**Context.** Prod is an always-on VM; a long-lived bot process already exists for
Telegram. Options: system cron + separate entrypoint, APScheduler in-process,
Cloud Scheduler hitting an endpoint.

**Decision.** APScheduler `BackgroundScheduler` inside the bot process.

**Rationale.** Gives one artifact that behaves identically on Docker and the VM
(serves "build for both"). Cron's decoupling advantage is real but solvable with
a `restart` policy (needed anyway), idempotency via a `runs` table, and a
startup catch-up check. Cloud Scheduler adds endpoints/auth/HTTP for no benefit
at one user. Splitting to cron later requires no change to `run_weekly()`.

*Update (ADR-013):* the immediate startup catch-up described here was removed —
see ADR-013 for why.

---

## ADR-007 — Messenger abstraction; long-polling; raw Bot API · Accepted

**Context.** Must be able to swap Telegram for WhatsApp later. Telegram offers
long-poll or webhook. Could use `python-telegram-bot` or raw Bot API.

**Decision.** A `Messenger` `Protocol` (`send`, `run(handler)`) with
`IncomingMessage` as the neutral inbound type. Telegram via **raw Bot API +
`httpx`, synchronous, long-polling**. Persist the update offset.

**Rationale.**
- Long-polling needs no public URL/TLS/firewall rules and works identically on
  Docker and the VM; webhook's real-time edge is irrelevant at one user.
- `python-telegram-bot` is framework-shaped (its own event loop, handler
  dispatch, and `ConversationHandler`) and would compete with the hand-written
  hot-path state machine — ceding the design the author wants to own. Its
  value-adds (offset mgmt, rate limiting) are trivial/irrelevant here.
- The core never imports the Telegram library; WhatsApp = one new class.

---

## ADR-008 — `diet.md` fully agent-managed, with versioned snapshots · Accepted

**Context.** `diet.md` is the user profile. The author does not want to hand-write
it; the agent should onboard (interview when empty) and fuse new info over time.

**Decision.** 100% agent-owned. Agent reads, merges old + new, rewrites. **Before
each rewrite, snapshot the previous version** to `data/diet.history/`.

**Rationale.** No hand-edits means no clobbering risk. But wholesale LLM rewrites
are *lossy* over time (can drop or soften details like a severe allergy).
Snapshots are free and make any mangling visible and reversible.

---

## ADR-009 — `symptoms.md` dropped; symptoms unified into the intake pipeline · Accepted

**Context.** Original plan had a `symptoms.md` file. But symptoms go through the
same structuring pipeline as meals.

**Decision.** No `symptoms.md`. Symptoms become structured, timestamped events in
the `symptoms` table via the same intake path as meals.

**Rationale.** Structuring-and-storing them *and* keeping loose markdown would
duplicate data. Structured symptom events are what the weekly correlation read
needs anyway.

---

## ADR-010 — `observations.md` as compressed long-term memory · Accepted

**Context.** The weekly run should consider medium/long-term patterns, not just
last week. Feeding all history every week grows cost/context without bound.

**Decision.** Weekly run reads `observations.md` (its compressed running summary)
plus the raw diary for a recent window (default 2–4 weeks), then
rewrites/appends `observations.md`.

**Rationale.** Bounds weekly cost and context regardless of how long the project
runs. Tradeoff: the model trusts its own past summaries; an early wrong
conclusion can persist. Mitigated by keeping the file human-readable and
correctable.

---

## ADR-011 — Testing: test the code around the model; test-after; pytest · Accepted

**Context.** Author is new to writing tests. LLMs are nondeterministic and cost
money.

**Decision.** Do not test model judgment. Test the deterministic code around it,
reaching the model and transport through fakes:
- Storage layer against in-memory SQLite (real).
- Pipeline branching with a `FakeLLM` returning canned outputs.
- The agent loop with a `FakeLLM` returning a scripted sequence of tool calls
  then a final answer (assert tools fired, loop terminates).
- Messenger flows against a `FakeMessenger`.
No model-output assertions in CI. `pytest`. Test-after (write code, then tests),
because the domain is still being learned.

**Rationale.** The same dependency-injection seams that allow provider/transport
swaps make the system fully testable without network or nondeterminism. This is
the minimum responsible coverage for a personal tool.

---

## ADR-012 — Versioned onboarding questionnaire with top-ups · Accepted

**Context.** Onboarding questions lived as prose inside the LLM prompt, and the
dispatch gate was binary (run onboarding only while `diet.md` is empty). So a
question added after a user onboarded would only ever reach new users; an existing
production profile could never gain it. We also had no way to retire a question and
clean up the answer it left behind (e.g. a raw `age: 35` once we switched to
deriving age from year of birth).

**Decision.** Declare the questionnaire as data in
`pipeline/onboarding_fields.py`: `ONBOARDING_FIELDS` (each with a `since` version)
and `DEPRECATED_FIELDS` (each with a `removed_in` version). `CURRENT_ONBOARDING_VERSION`
is derived from the largest version mentioned. A profile records the version it was
built from in the `kv` table (`onboarding_version`; absent ⇒ treated as v1). The
same LLM onboarding code drives the top-up: it asks only `fields_since(stored)`,
strips `removed_since(stored)` from the rewritten `diet.md`, and bumps the version.

**Scheduling.** A top-up never interrupts the user. On each message we first finish
whatever they started — and only *after* their process is fully complete (no
meal/symptom left mid-clarification, i.e. no active session) do we open the top-up,
while they're still online. If their action left a pending session, we try again on
the next message. `skip` defers the top-up *without* recording the version, so it
returns next time the user is idle (versus completing it, which bumps the version).
A retirement-only change (no new questions) is applied silently. Onboarding stays
LLM-driven and diet.md agent-owned (ADR-008); the registry only supplies the
version diff the model can't compute.

**Rationale.** Mirrors the additive DB migrations in `storage/db.py` (`_migrate`):
the schema of *questions* now evolves the same way the schema of *tables* does, so
the questionnaire can change over the life of a long-running deployment without
re-interviewing users or orphaning stale answers.

---

## ADR-013 — Drop the immediate startup catch-up for the weekly review · Accepted

**Context.** ADR-006 added a synchronous catch-up call right after `start_scheduler`
at boot: if a review was overdue, it fired immediately, at whatever wall-clock hour
the process happened to restart. In practice, a restart (e.g. deploying a change)
at 3am fired the review at 3am — the catch-up had no notion of a sane hour, only
"is it overdue."

**Decision.** Remove the immediate catch-up call. `start_scheduler`'s `CronTrigger`
already computes its next fire time as the next match strictly after the moment it's
created — today's `WEEKLY_CRON` time if the process starts before it, tomorrow's
otherwise. That's sufficient on its own: a review overdue at boot simply waits for
that next tick, arriving at a normal hour instead of whenever the process happened
to restart.

**Rationale.** The original worry (ADR-006) was a review silently missed forever if
a restart straddled the exact cron tick. But even without the immediate catch-up,
the cron trigger still fires within one day of boot in the worst case — a bounded
delay proportional to how long the process was actually down, not an indefinite
loss. That's an acceptable, expected trade for a single-user personal deployment,
and it's strictly better than surprising the user with a report at 3am.

---

## ADR-014 — The app owns the numbers; the agent owns the prose · Accepted

**Context.** An outside model reading a 90-day export found the weekly agent had
written 1899 kcal and 78g protein into `observations.md` for a week the diary puts
at 2098 and 89g. That figure then entered the next run as established fact and
shaped the advice built on it. The system prompt already forbade exactly this — it
says every calorie/protein/fibre figure must come from `get_intake_summary` and that
model arithmetic is not trustworthy. It was ignored anyway. The same review found a
"persistent deficit" framing that had been true a month earlier and was carried
forward unchecked.

**Decision.** Stop relying on instruction for anything checkable.

1. Every number the agent can quote is computed in SQL first — per-day and per-week
   averages for *all six* macros (not just calories/protein/fibre, which left a
   rising sugar trend literally unseeable), food frequency with the logged-day count
   as denominator, symptom-day comparisons, evening calorie share, and the date each
   nutrient started being recorded.
2. The app writes the figures into `observations.md` itself, inside a delimited block
   it replaces every run, and tells the agent the block wins any disagreement with
   its prose. The agent rewrites that file wholesale each week; anything it keeps is
   retyped from memory, and a retyped number drifts.
3. `run_weekly` checks the report's calorie claims against computed values and logs
   any it cannot account for (`agent/verify.py`).

**Rationale.** A prompt line is a request; a computed value is a fact. Where the two
compete, the fact should not depend on the model choosing to honour the request —
particularly here, where a wrong number does not merely make one sentence wrong but
becomes the following week's premise. The verification is deliberately narrow
(calorie figures in a plausible daily-intake range only) so that a legitimate
recommendation — "add a 200 kcal snack" — is never flagged as a false claim; it
catches invented averages, not every possible slip. Tradeoff: the memory file is no
longer purely agent-authored, and the app's block costs context on every run. Both
are cheap next to compounding a wrong average week after week.

---

## ADR-015 — Two exports: a backup to restore, a briefing to read · Accepted

**Context.** `/export` produced JSON containing meals, symptoms, exercises and saved
foods — and nothing else. It carried no body metrics, no targets, no weight log and
neither memory file, so it could not actually restore the app, and a model handed it
could total calories but had no idea whose they were. Meanwhile a weekly summary
cannot see across a quarter, and the app has no way to ask a larger model to look.

**Decision.** Split by audience. `/exportbackup` (was `/export`) stays
machine-shaped: versioned JSON, now including metrics, both targets, weight and waist
logs, timezone, onboarding version, stored weekly reports and the memory files with
`diet.md`'s history snapshots. `/exportdeepdive` is a Markdown briefing over the last
90 days — context first (who this is, what they're aiming at), arithmetic already
done, every meal in the user's own words, and an explicit statement of the data's own
weak spots. Weekly reports are persisted (`weekly_reports`) specifically so this
export gets richer over time; they used to be sent to Telegram and forgotten.

**Rationale.** The two have opposite requirements. A backup format must stay stable
and parseable for a future importer; a briefing should be free to be reworded
whenever it reads better, and is written for judgement rather than restoration.
Merging them would either freeze the prose or destabilise the restore path. Markdown
for the briefing costs roughly half the tokens of equivalent JSON at the same
fidelity, and 90 days keeps the file inside one context window as years accumulate —
what falls outside the window is stated, not silently dropped.

---

## ADR-016 — A derived nutrition watchlist, covered on rotation · Accepted

**Context.** The weekly review only ever discussed what the user had already told it
to care about: goals and notes. Everything they never thought to ask about stayed
invisible — a vegan diary was never checked for iodine or selenium, and the family
history captured at onboarding (heart disease, hypertension) was read by nothing at
all, even while the diary filled with high-sodium processed food. The report could
reflect the user's worries back at them, but not add to them.

**Decision.** The agent derives a watchlist from the *whole* profile — diet style,
medical conditions, family history, age/sex, location, and the supplements already
being taken — and persists it (kv, `nutrition_watchlist`), each item carrying the
profile evidence behind it. `get_watchlist` names the one or two least recently
covered items as due; the report goes deep on those alone and calls
`mark_watchlist_reviewed`. Re-deriving after a profile change preserves the rotation
dates. Sodium became a tracked macro in the same change so the most common of these
risks is measurable rather than guessed at.

**Rationale.** Persisted rather than re-derived each week for the reason ADR-014
gives for calibrated targets: a good derivation that lives only in one message is
lost the moment it is sent. Rotation is what makes the feature survivable — a report
reciting nine nutrients every week is one the user learns to skip, and skipping it
costs them the weeks where an item genuinely mattered. Supplements are read first so
the agent cannot lecture someone about a nutrient they already take daily. The
measured-vs-inferred rule still binds: these are inferences from food names, and
anything clinical is framed as what a blood test or a doctor settles.

**Multi-user.** Nothing here is user-specific. The code asks what a given profile
implies; the answer lives in that user's own database. A profile with no cardiac
history simply never gets sodium on its list.
