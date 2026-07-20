# xirtun

A personal nutrition assistant you talk to over **Telegram**. Log meals, symptoms,
and workouts in plain language — by text *or* voice — and xirtun estimates nutrition,
remembers your profile, learns the foods you buy, and once a week proactively messages
you with patterns, risks, and concrete suggestions it found in your own data.

It's single-user and self-hosted: it runs on your own machine or a small VM, keeps all
your data in plain files you own, and talks to no one but you and the model provider.

> **Disclaimer:** xirtun is a personal, informational tool — not a medical device.
> Its nutrition figures are rough estimates and its observations are not medical
> advice, diagnosis, or treatment. Always consult a qualified healthcare professional
> about any health concern.

---

## What it can do

**Logging (text or voice, in plain language)**
- **Meals** — *"leftover curry, ~2 cups, and a beer"* becomes structured, timestamped
  items with estimated calories and macros (protein, fat, carbs incl. sugars, fibre).
  Composite foods are broken into ingredients (a sandwich → bread, chicken, mayo,
  lettuce) and tagged with likely allergens/sensitivities (dairy, gluten, soy, egg,
  nuts, FODMAP, …).
- **Symptoms** — *"I've been bloated since this morning"* → a structured, timestamped
  symptom with optional severity and duration.
- **Exercise** — *"ran 5k this morning"* or *"45 min of legs at the gym"* → a
  structured activity with duration, intensity, distance, and an estimated calorie
  burn (from your body weight).
- **Clarifying questions** — if a description is too vague to estimate, it asks one
  short follow-up instead of guessing. Send `cancel` (or `nevermind`/`stop`) any time
  to drop it instead of answering.
- **Smart timing** — infers *when* something happened ("lunch", "this morning",
  "yesterday") in your timezone, separate from when you logged it. One message can
  describe several eating occasions at different times.
- **Voice notes** — speak instead of typing; the audio is transcribed and flows
  through the exact same pipeline.

**Your profile (`diet.md`, agent-managed)**
- A first-run **interview** captures sex, year of birth, height, weight, activity
  level, allergies, conditions, family history, diet style, supplements, and goals.
- Add to it anytime as a **note** — *"I want to gain muscle"*, *"I exercise twice a
  week"*, *"I want more lutein"* — and the weekly review factors it in.
- Update your **weight** (`/addweight`) or describe your **activity level** in plain words
  (`/setactivity I train hard 3 days and walk the rest`); targets recompute automatically.
- New facts are merged in over time and the old version is snapshotted before each
  rewrite (so nothing is silently lost).

**Custom food database**
- Save the exact label nutrition for foods you buy often: *"save Lidl vegan sausage:
  200g package, per 100g — 214 kcal, 23g protein, 9g fat, 6g carbs, 4.6g fibre"*.
- When you later log that food, its macros are computed **exactly** from the label
  (per-100g × grams, or a whole package) instead of being estimated.
- Duplicate-aware: if a similar food already exists it asks **update / add / cancel**.

**Custom meals (recipes)**
- Save a recurring meal once — *"/savemeal breakfast cereals: 75g muesli, 250ml oat
  milk, 30g protein powder"* — then log it later just by name (*"I ate breakfast
  cereals"*) and it expands to all of its stored items.

**Targets & stats (deterministic — no LLM, no cost)**
- **Daily calorie + protein targets** computed from your metrics (Mifflin–St Jeor) —
  but that formula is only the *prior*: the weekly review **calibrates a working
  target** from real evidence (weight trend, satiety feedback, injuries/activity
  changes) and persists it, clamped to safe physiological bounds. `/target` shows
  both numbers and the rationale for the current calibration.
- **`/today`** and **`/week`** summaries with real totals and per-day averages.

**Proactive help**
- **Weekly autonomous review** — a tool-using agent reviews your recent diary, your
  profile, your targets, your **weight trend**, and its own past notes, then sends a
  **structured report** (overview, energy & macros, food quality, nutrient wins,
  watch-outs, actions) with non-obvious patterns and **actionable** suggestions, framed
  as things to look into or raise with a doctor — never a diagnosis. It compares this
  week against recent weeks (**week-over-week**, computed in SQL) and gives a plain
  verdict on how well you actually ate. It treats the calorie target as an
  *estimate* and reconciles it against your weight trend and goal, so it won't tell you
  to eat more while your weight is steady or rising — and when the evidence justifies
  it, it **persists a recalibrated working target** (small steps, safe bounds, with its
  reasoning) instead of re-deriving the same adjustment in prose every week.
  - It only asks a calibrating question when the answer would actually change next
    week's analysis (e.g. resolving a logged-intake-vs-weight-trend conflict) — never a
    generic check-in.
  - **`/weekly`** (you're at the keyboard): questions come *first* — the report is held
    until you answer or send `skip`/`done`. You can log a meal/symptom/etc. in between;
    it's processed normally and the open question resurfaces afterward.
  - **Scheduled run** (no one's guaranteed to be around): the report sends immediately;
    any questions follow as a separate message, and your reply (whenever it comes) is
    saved as a note for **next** week's analysis rather than reprocessing this one.
- **Weight-log reminder** — on the morning your weekly review is due, if you haven't
  logged a weight in the last 6 days, it nudges you to send `/addweight` so the review can
  judge your calories against the scale instead of just a formula.
- **Late-meal nudge** — log a meal eaten after 20:00 in (near) real time and it replies
  once per evening with a reminder to stay upright for 2–3 hours (reflux prevention at
  the moment it matters, not in Sunday's retrospective). Backdated entries don't
  trigger it.
- **Shopping-list assistant** — *"heading to the shop, what should I grab?"* →
  suggestions drawn from your goals, recent diet, and gaps (and it won't suggest what
  you already ate this week).

**Housekeeping**
- Undo (with confirmation), export your whole diary to JSON, wipe everything (with
  confirmation), view your profile, and a slash-command menu in the Telegram client.

---

## Commands

| Command | What it does |
|---|---|
| *(just type or speak)* | Log a meal/symptom/workout, add a note, ask for a shopping list, or save a food — all in plain language |
| `/addmeal` | Start a fresh multi-message meal entry |
| `/addworkout` | Log a workout |
| `/addsymptom <text>` | Log how you feel |
| `/addnote <text>` | Save a note or goal for your weekly review |
| `/undo` | Remove your last logged entry (asks to confirm, shows what it'll delete) |
| `/today` | Today's meals and totals |
| `/week` | The past 7 days, with per-day averages |
| `/lastmeals` · `/lastsymptoms` · `/lastworkouts` · `/lastnotes` | Your last 3 of each, with times — to check what you've already logged |
| `/shop` | Suggest a shopping list |
| `/savefood <name>: <per-100g nutrition>` | Save a food's label (with package size + fibre) |
| `/foodlist` | List your saved foods |
| `/checkfood <name>` | Check whether a food is saved (exact + similar matches) |
| `/delfood <name>` | Remove a saved food |
| `/savemeal <name>: <ingredients>` | Save a recurring meal (recipe) |
| `/meallist` | List your saved meals |
| `/delmeal <name>` | Remove a saved meal |
| `/target` | Formula estimate + your calibrated working target (with why), plus your recent weight trend |
| `/addweight <kg>` | Update your weight (keeps targets current) |
| `/setactivity <description>` | Update your activity level in plain language (recomputes targets) |
| `/weekly` | Run the weekly review right now |
| `/settimezone <IANA name>` | Set your timezone, e.g. `/settimezone Europe/Ljubljana` — takes effect immediately, no restart |
| `/profile` | Show your profile and body metrics |
| `/export` | Export your full diary (meals, symptoms, foods) as JSON |
| `/cleardata` | Erase all your data (asks to confirm) |
| `/skip` | Skip a weekly-review calibration question |
| `/help` | What I can do |

---

## How it works

Two loops, deliberately separated:

- **Hot path** (every inbound message): a deterministic state machine —
  `classify → clarify? → structure → store`. The cheap model handles intent
  classification and structuring; commands and stats are pure Python (no model calls).
- **Weekly review**: a ReAct-style **agent loop** that's given tools
  (`query_diary`, read/write `observations`, read/update the profile, `get_targets`)
  and autonomously decides which to call, what to conclude, and whether to message you
  at all. The scheduler only *triggers* it — the decisions live in the loop.

Boundaries that keep it swappable and fully testable:

- `messaging/` — a `Messenger` protocol; Telegram via the raw Bot API (long-polling,
  no inbound ports). Voice notes are downloaded and transcribed in the transport, then
  handed to the pipeline as ordinary text.
- `llm/` — an `LLMClient` protocol; Gemini today, with a **cheap** model on the hot
  path and a **strong** model for the weekly review. The adapter handles structured
  output, audio transcription, and transient-error retries with backoff.
- `storage/` — SQLite via the standard library; no ORM. Lightweight migrations.
- `memory/` — `diet.md` (your profile) and `observations.md` (the agent's long-term
  memory): durable, human-readable Markdown.

The weekly run is **idempotent** (tracked in a `runs` table), so a manual run and the
scheduled one can't double-fire. If a review is overdue when the process restarts, it
waits for the next `WEEKLY_CRON` tick (today or tomorrow) rather than firing
immediately at whatever hour the restart happened to land on. The full design and
decision log are in [`docs/`](docs/).

---

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then fill in the values
```

| Variable | Purpose |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Your Telegram chat id (e.g. via [@userinfobot](https://t.me/userinfobot)) |
| `GEMINI_API_KEY` | Google AI Studio API key |
| `LLM_CHEAP_MODEL` | Hot-path model (default `gemini-2.5-flash-lite`; `gemini-2.5-flash` is more reliable for structured output and audio) |
| `LLM_STRONG_MODEL` | Weekly-review model (default `gemini-2.5-pro`) |
| `WEEKLY_CRON` | When to check for the weekly review (default `0 17 * * *`, daily at 17:00 — runs if 7 days have passed since the last review) |
| `WEIGHT_REMINDER_CRON` | When to check whether to nudge for a weight log (default `0 8 * * *`, daily at 08:00 — only nudges on the morning the review is due and if no weight was logged in 6 days; keep earlier than `WEEKLY_CRON`) |
| `DATA_DIR` | Where the SQLite DB and Markdown files live (default `./data`) |

Timezone isn't an env var: it defaults to UTC and is set from the onboarding interview
(inferred from wherever you say you live), stored in the DB so meal times, the weekly
cron, and the weight reminder all use it. Change it any time with `/settimezone`.

Everything in `DATA_DIR` (`xirtun.db`, `diet.md`, `observations.md`, `diet.history/`)
is created at runtime and is gitignored — never committed.

## Running

```bash
uv run python -m xirtun.main          # the bot: long-polling + in-process weekly scheduler
uv run python -m xirtun.run_weekly    # run the weekly review once, now
uv run python -m xirtun.run_reminder  # send the weight-log reminder now (if it's due)
```

Shortcuts are also available via the `Makefile` (`make dev`, `make weekly`,
`make check`); run `make` to list targets.

On first launch the bot interviews you to build your profile; after that, just talk to
it.

## Testing

```bash
uv run pytest        # ~100 tests, fully offline
uv run ruff check    # lint
```

The LLM and messaging layers are replaced with fakes, so the intake pipeline, the
agent loop, storage, and every command are covered deterministically — **no network
calls and no API cost**.

## Project layout

```
src/xirtun/
  config.py          env-driven configuration, validated at startup
  main.py            bot entrypoint (intake + scheduler + command menu)
  run_weekly.py      weekly-review entrypoint (guarded + idempotent)
  run_reminder.py    morning weight-log reminder (fires the day the review is due)
  scheduler.py       APScheduler weekly + weight-reminder triggers
  reports.py         deterministic /today and /week reports
  targets.py         calorie/protein targets (Mifflin–St Jeor)
  export.py          /export diary dump
  messaging/         Messenger protocol + Telegram transport (incl. voice)
  llm/               LLMClient protocol + Gemini adapter (structured output, audio, retries)
  storage/           SQLite: diary, custom foods, runs, admin/reset
  memory/            diet.md / observations.md read/write
  pipeline/          hot path: classify, structure, symptom, shopping, food,
                     onboarding, sessions, intake (the state machine)
  agent/             the weekly agent loop and its tools
tests/               offline tests (fakes for LLM + messaging)
docs/                product, architecture, decisions, roadmap
```

## Tech

Python 3.12 · [uv](https://docs.astral.sh/uv/) · Google Gemini (`google-genai`) ·
`httpx` · `APScheduler` · `pydantic` · SQLite (stdlib) · `pytest` · `ruff`. Synchronous
throughout; no async and no web framework.

## Status

A personal project, single-user by design. See [`docs/roadmap.md`](docs/roadmap.md)
for what's planned next (e.g. photo input, deeper coaching, deploy automation).
Licensed under [MIT](LICENSE).
