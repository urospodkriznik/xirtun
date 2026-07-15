# Known issues

Open bugs, tracked with repro + root cause so they don't need re-diagnosing.
Not yet scheduled — no priority order implied by list position.

_All issues below currently resolved. Kept as a record; add new ones above the line._

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
