# Known issues

Open bugs, tracked with repro + root cause so they don't need re-diagnosing.
Not yet scheduled — no priority order implied by list position.

_Issues 4-6 are **open**, and are about data already in the diary rather than code:
the guards added for each only apply to entries made from then on. Issues 1-3 are
resolved, kept as a record._

---

## 4. Meals logged before 2026-08-24 understate stated portion counts · open

**Repro.** In the live diary, "3 portions of pasta with broccoli, a small glass of
beer" logged 485 kcal; "a portion of pasta with broccoli, a small glass of beer"
logged 490. Restating the amount emphatically ("it really was a lot, two very big
portions") changed nothing.

**Root cause.** The structurer prompt never said to scale by a stated count, only
that estimates are rough. Measured against the API: the cheap model put 3 portions at
1.35x one portion (pasta 150g -> 225g); the strong model got it roughly right. Adding
an explicit quantity block fixed the cheap model too (150g -> 450g, exactly 3x), so
this was a prompt gap, not a model limitation.

**Status.** Fixed forward in `structure.py`, and the acknowledgement now prints the
assumed grams so the next one is catchable. Rows logged before that remain
understated by an unknown factor — anything reading intake across that boundary is
comparing two different estimation regimes.

## 5. One meal_item stores a portion size in the fat field · open

**Repro.** 2026-06-23, `falafel`: 240g portion, 600 kcal, 240g fat. Those macros imply
2832 kcal. It single-handedly moves that fortnight's fat average from ~65g to ~85g.

**Root cause.** An estimation slip the schema had no reason to reject.

**Status.** `structure.py` now retries such an item once before storing, and
`/exportdeepdive` lists any that got through under "Rows that don't add up". The
existing row is untouched — nothing rewrites logged data.

## 6. Sugar and fibre are absent from entries predating their columns · open

**Repro.** Every week in the live database sums to `0g fibre`.

**Root cause.** Both columns were added by migration after logging began, so earlier
rows hold NULL. Read bare, that is indistinguishable from measured zero.

**Status.** `get_intake_summary` now reports when each nutrient started being
recorded, and says explicitly when one has *never* been recorded so the zeros aren't
read as a shortfall. The underlying rows stay empty; averages spanning that boundary
still understate, and only re-estimating old entries would change that.

---

## ~~1. Unrecognized slash commands silently fall through to free-text classification~~ (fixed)

**Repro.**
```
/note i still alway feel bloated. im not going to toilet regurarly
→ Symptom logged: bloating (20:33, chronic).
```

**Root cause.** The commands were renamed at some point to `/addnote` and
`/addsymptom` (`intake.py` only matches `text.startswith("/addnote")` /
`"/addsymptom"` now). A message starting with `/` that doesn't match any known
command prefix isn't rejected — it falls straight through to intent
classification, so `/note ...` gets read as plain text and misclassified (here,
as a symptom).

**Fix.** `handle_message` now rejects any leftover message starting with `/`
(one that matched no command and isn't a session reply) with "Sorry, I don't
recognize that command. Send /help to see what I can do." instead of
classifying it. No aliases were added for the old names — a wrong command is
now surfaced rather than guessed. (`test_unrecognized_slash_command_is_rejected`)

---

## ~~2. Late-meal reflux nudge fires in cases where it isn't useful~~ (fixed)

**Repro A — beverage-only entry:**
```
/meal i had 0.5l beer  (20:31)
→ 🌙 Late meal — try to stay upright...
```

**Repro B — recap logged well after eating:**
```
at 9pm i ate outside pasta with mushrooms and 4 small bruschette. then i had
two beers, together 0.8l   (occurred_at inferred 21:00, but message plausibly
sent well after — a same-evening recap, not real-time logging)
→ 🌙 Late meal — try to stay upright...
```

**Root cause** (`intake.py::_maybe_late_meal_nudge`):
- The check only looked at `occurred_at` hour + a 3-hour recency window — it
  never looked at *what* was logged, so a beverage-only entry got the same
  "full stomach / stay upright" framing as a solid meal.
- The 3-hour "recent" window was generous enough that a same-evening recap
  (typed well after the fact) still counted as "real-time" and fired.

**Fix.**
- Beverage-only meals are skipped (`_is_beverage_only` — every item name matches
  a drink keyword). Decision taken: exclude entirely rather than reword.
  (`test_late_beverage_only_meal_gets_no_nudge`)
- The recency window was tightened from 3h to 90 min (`LATE_MEAL_NUDGE_WINDOW`),
  so a recap logged hours later no longer fires.
  (`test_late_meal_recap_logged_hours_later_gets_no_nudge`)

---

## ~~3. Saved custom meals ignore a stated portion fraction on expansion~~ (fixed)

**Repro:**
```
/addmeal i ate 2/3 of portion of breakfast cereal
→ Total: ~970 kcal ...

/addmeal 1/2 of portion of breakfast cereal
→ Total: ~970 kcal ...   (identical — full recipe both times)
```

**Root cause.** `_expand_custom_meals` (`intake.py`) swapped a `custom_meal`
placeholder item for the saved recipe's stored items verbatim
(`expanded.extend(recipe["items"])`) with no scaling, and the structurer had no
field to record the portion eaten.

**Fix.** Added a `portion` field to the `Item` model (fraction eaten, 1 = full);
the structurer sets it for partial custom-meal references, and
`_expand_custom_meals` scales every expanded item's quantity/macros by that
factor via `_scale_item`.
(`test_log_partial_portion_of_saved_meal_scales_macros`,
`test_log_full_portion_of_saved_meal_unscaled`)
