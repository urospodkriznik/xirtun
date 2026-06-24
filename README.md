# xirtun

A personal nutrition assistant you talk to over Telegram. Log meals and symptoms in
plain language; xirtun estimates nutrition, remembers your profile, and once a week
proactively messages you with patterns, risks, and concrete suggestions it found in
your own data.

It's single-user, runs on your own machine or a small VM, and is written in plain
Python — the agent loop is hand-written, with no agent framework.

> **Disclaimer:** xirtun is a personal, informational tool — not a medical device.
> Its nutrition figures are rough estimates and its observations are not medical
> advice, diagnosis, or treatment. Always consult a qualified healthcare
> professional about any health concern.

## What it does

- **Conversational logging.** "I had a chicken wrap for lunch and felt bloated this
  morning" becomes structured, timestamped meals and symptoms, with estimated
  calories, macros, and allergen/sensitivity tags. It asks a follow-up question when
  a description is too vague to estimate, and infers *when* something happened from
  cues like "lunch" or "yesterday".
- **An agent-managed profile** (`diet.md`). A first-run interview captures your age,
  body metrics, allergies, conditions, diet, and goals. You can add to it any time —
  "I want to gain muscle", "I exercise twice a week", "I want more lutein" — and the
  weekly review takes it into account.
- **A weekly autonomous review.** Once a week a tool-using agent reads your recent
  diary, your profile, and its own past notes, then sends a short message with
  non-obvious patterns and actionable recommendations — framed as things to look
  into or raise with a doctor, never as a diagnosis.

## Architecture

Two loops:

- **Hot path** (every message): a deterministic state machine —
  `classify → clarify? → structure → store`.
- **Weekly review**: a hand-written, ReAct-style agent loop that calls tools
  (`query_diary`, read/write `observations`, read/update the profile) and decides for
  itself what — if anything — is worth telling you.

Key boundaries keep the system swappable and fully testable:

- `messaging/` — a `Messenger` protocol; Telegram (raw Bot API, long-polling) today.
- `llm/` — an `LLMClient` protocol; Gemini today, with a cheap model on the hot path
  and a strong model for the weekly review.
- `storage/` — SQLite via the standard library; no ORM.
- `memory/` — `diet.md` and `observations.md`: durable, human-readable agent memory.

The full design and the decision log live in [`docs/`](docs/).

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then fill in the values
```

| Variable | Purpose |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat id |
| `GEMINI_API_KEY` | Google AI Studio API key |
| `LLM_CHEAP_MODEL` / `LLM_STRONG_MODEL` | Model names (sensible defaults provided) |
| `TIMEZONE` | IANA name, e.g. `Europe/Ljubljana` |
| `WEEKLY_CRON` | Weekly-review schedule (default Sunday 09:00) |

## Running

```bash
uv run python -m xirtun.main          # the bot (Telegram long-polling)
uv run python -m xirtun.run_weekly    # run the weekly review now
```

Or via the `Makefile` shortcuts: `make dev`, `make weekly`, `make check`
(lint + tests). Run `make` to see all targets.

## Testing

```bash
uv run pytest
```

Tests are fully offline: the LLM and messaging layers are replaced with fakes, so the
intake pipeline, the agent loop, and storage are covered deterministically with no
network calls or API cost.

## Project layout

```
src/xirtun/
  config.py          configuration (env-driven, validated at startup)
  main.py            bot entrypoint
  run_weekly.py      weekly-review entrypoint
  messaging/         Messenger protocol + Telegram transport
  llm/               LLMClient protocol + Gemini adapter
  storage/           SQLite access
  memory/            diet.md / observations.md
  pipeline/          hot-path intake: classify, structure, sessions, onboarding
  agent/             the weekly agent loop and its tools
tests/
docs/                product, architecture, decisions, roadmap
```

## Status

A personal project, single-user by design. See [`docs/roadmap.md`](docs/roadmap.md)
for what's planned next.
