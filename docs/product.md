# Product — Personal Nutritionist Agent ("xirtun")

> Permanent project memory. This document defines *what* we are building and *why*.
> For *how*, see [architecture.md](architecture.md). For locked vs. future scope, see
> [roadmap.md](roadmap.md). For the reasoning behind specific choices, see
> [decisions.md](decisions.md).

## One-line description

A personal nutritionist agent, used by a single person (the author), that logs
meals and symptoms over Telegram, estimates approximate nutrition, and once a
week proactively messages back patterns, risks, and suggestions it noticed.

## The problem it solves

The author wants a low-friction way to:

1. Record what they eat and how they feel, by just typing into Telegram.
2. Get a *rough* sense of nutrition (calories + macros) without manual tracking.
3. Have something **notice non-obvious patterns over the medium-to-long term** —
   e.g. "your bloating reports cluster after dairy," or "saturated fat is
   consistently high and protein low," or "given these symptoms over months, it
   might be worth asking your doctor about a colon screening / iron panel."

The value is **not** precise calorie counting. It is **surfacing things the
author would not spot on their own**, framed as prompts to investigate — never
as diagnosis.

## Who it is for

One user: the author. Single-tenant. No accounts, no multi-user, no sharing.
This assumption is load-bearing — it justifies skipping auth, per-user data
isolation, rate limiting, and webhooks (see [decisions.md](decisions.md)).

## In scope — v1 (LOCKED)

1. **Meal logging via Telegram text.** Free-text in ("leftover curry, ~2 cups,
   and a beer"). A cheap LLM classifies, asks brief clarifying questions when the
   description is too vague to estimate, then stores a structured record.
2. **Approximate nutrition estimation.** LLM-only. Calories + macros per item.
   ±20–30% is acceptable and expected (see [decisions.md](decisions.md), ADR-003).
3. **`diet.md` — an agent-managed user profile.** Allergies, conditions, family
   history, diet style, supplements, goals. The agent runs an onboarding
   interview when it is empty and merges in things the user mentions later.
4. **Symptom logging** through the *same* intake pipeline as meals ("I've been
   bloated since this morning" → structured, timestamped symptom event).
5. **Weekly autonomous summary.** Once a week, with no prompting, a strong
   big-context model runs a real agent loop: it reviews recent history plus its
   own accumulated long-term memory (`observations.md`), decides whether anything
   is worth saying, and sends a Telegram message. This is the mandatory
   autonomous behavior.

## Out of scope — v1 (deferred to v2; see roadmap.md)

- Voice input (planned v2) and photo input.
- Deterministic stats / charts (e.g. a `/today` or `/week` command computing
  exact totals).
- A structured food database (USDA FDC, OpenFoodFacts) for accuracy.
- Controlled allergen/ingredient tag vocabulary and `json_each`-based querying.
- Statistical/deterministic correlation. v1 correlation is the LLM *eyeballing*
  a window of history, not a statistical claim.
- Agent-planted follow-up questions on the hot path ("does this burger contain
  soy?" driven by `observations.md`).
- Webhook transport (v1 uses long-polling).
- Multi-user, accounts, auth.

## Non-goals / philosophy

- **This is a helper, not a professional tracker.** Approximation is fine.
- **It is not a medical device and gives no diagnosis.** It may *suggest*
  discussing something with a doctor or getting a test, always hedged. Any
  health-adjacent observation must be framed as "worth investigating," not
  "you have X." This framing is a hard requirement on the weekly run's prompt.
- **The author owns the agent loop.** No agent frameworks (no LangChain,
  LlamaIndex, etc.). Raw Python. This is also a learning project for Python
  fluency, so clarity beats cleverness everywhere.

## Success criteria for v1

- The author can log a meal or symptom in one message and, when needed, answer
  one or two clarifying questions; the event is stored structured.
- The weekly message arrives on schedule without prompting and says something
  the author finds at least occasionally *useful or non-obvious*.
- `diet.md` and `observations.md` accumulate sensible content over weeks.
- Monthly running cost stays in the ~$1–2 LLM + ~$6–15 VM range
  (see [architecture.md](architecture.md) cost section).
- The author understands every line of the code (no opaque framework magic).
