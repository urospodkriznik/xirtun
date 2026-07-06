# Architecture

> *How* the system is built. For *what* and *why-at-the-product-level*, see
> [product.md](product.md). For the rationale behind each technical choice, see
> [decisions.md](decisions.md).

## Guiding constraints

- Raw Python, no agent frameworks. The author owns the agent loop.
- Single user. No auth, no multi-tenancy.
- Two-tier LLM cost model: cheap model on every message, strong model once a week.
- Runs identically on local Docker (dev) and a GCP VM (prod). One artifact.
- Everything the model touches is reached through an abstraction, so providers
  can be swapped **and** the code is testable with fakes.

## Component map

```
                         Telegram (long-poll)
                                 |
                                 v
        +--------------------------------------------------+
        |              messaging/  (Messenger)             |
        |   TelegramMessenger  <-- interface -->  Fake     |
        |   normalizes inbound -> IncomingMessage          |
        +-----------------------+--------------------------+
                                | IncomingMessage / send(text)
                                v
        +--------------------------------------------------+
        |   pipeline/  HOT PATH  (state machine, reactive)  |
        |   classify -> clarify? -> structure -> store      |
        |   uses: cheap LLMClient, sessions (pending meal)  |
        +------+----------------------------+--------------+
               |                            |
       reads/writes                  reads/writes
               v                            v
   +---------------------+      +---------------------------+
   |  storage/ (SQLite)  |      |  memory/  (markdown files) |
   |  meals, meal_items, |      |  diet.md (agent-managed)   |
   |  symptoms, runs,    |      |  observations.md (agent)   |
   |  pending            |      |  diet.history/ snapshots   |
   +----------+----------+      +-------------+-------------+
              ^                               ^
              |                               |
        +-----+-------------------------------+-----+
        |   agent/  WEEKLY RUN (agentic tool loop)   |
        |   strong LLMClient + tool dispatch loop    |
        |   tools: query_diary, read/append          |
        |          observations, read/merge diet,    |
        |          send_message                      |
        +---------------------+----------------------+
                              ^
                              |
                    +---------+---------+
                    | scheduler/         |
                    | APScheduler (in    |
                    | bot process)       |
                    +--------------------+
```

`main.py` wires it together: starts the Telegram long-poll loop (foreground) and
the APScheduler weekly trigger (background thread) in one process, supervised by
Docker `restart: always`.

## The two loops (this is the heart of the design)

### Loop 1 — Hot path: a state machine, NOT an agent

Reactive, user-in-the-loop. The model does not autonomously choose actions; the
loop advances on the user's replies. Deliberately constrained — making meal
logging a free-form tool loop would burn tokens, add latency, and be
unpredictable for a well-defined task.

```
on IncomingMessage:
    if message is a command (/meal, /undo, /export, ...): handle directly
    else:
        intent = cheap_llm.classify(message, diet.md, pending_session?)
            -> one of: meal | symptom | correction | other | continue_meal
        if intent needs more detail to estimate:
            ask ONE focused follow-up; keep/append pending session; return
        else:
            structured = cheap_llm.structure(full meal/symptom text, schema)
            store(structured); acknowledge
            close pending session
```

Pending-meal boundaries:
- Explicit start: user sends `/meal`.
- Safety nets, because the user will forget: (a) a pending session auto-closes
  after ~30 min of inactivity; (b) the classifier may return `meal` (new) rather
  than `continue_meal`, which closes the previous session automatically.

### Loop 2 — Weekly run: a real agent loop

Proactive, no user in the loop. This is where "agent" is earned: the model is
given tools and **autonomously decides** which to call, in what order, whether a
pattern is strong enough to mention, and whether to message the user at all. The
scheduler only *triggers* it — a scheduled trigger no more makes the behavior
non-autonomous than an alarm clock makes a person's morning decisions
non-autonomous.

The loop the author writes by hand (no framework):

```
context = [system_prompt, diet.md, observations.md]
for i in range(MAX_ITERS):
    resp = strong_llm.complete(context, tools=TOOLS)
    if resp is a tool call:
        result = dispatch(resp.tool_name, resp.args)   # dict name -> python fn
        context.append(resp); context.append(result)
        continue
    else:                       # model emitted its final actions / message
        break
```

Tools exposed to the weekly agent (v1):

| Tool | Purpose |
|---|---|
| `query_diary(since, until, kind?, tag?, food?)` | pull windows/slices of meals & symptoms |
| `read_observations()` / `append_observation(text)` | its long-term memory |
| `read_diet()` / `merge_diet(text)` | record things the user forgot to mention (snapshots first) |
| `send_message(text)` | the proactive Telegram message |

`MAX_ITERS` is a hard safety cap on the loop. The run is **idempotent** and
records itself in a `runs` table (see schema) so a missed week is caught on next
boot and a double-trigger is harmless.

### Long-term memory model (bounds weekly cost)

Feeding *all* history into the weekly prompt grows unbounded forever. Instead:

- The weekly run reads `observations.md` (its **compressed running summary** of
  everything concluded so far) **plus** the *raw* diary for a recent window
  (default last 2–4 weeks, via `query_diary`).
- It rewrites/appends `observations.md` with new conclusions.

Tradeoff accepted: the model trusts its own past summaries, so an early wrong
conclusion can persist. Acceptable for a personal helper; mitigated by keeping
`observations.md` human-readable so the author can correct it.

## Messaging abstraction

Outbound is uniform; inbound transport varies (long-poll vs webhook). The
boundary: a transport adapter converts provider events into one neutral object,
and the core only ever sees that object plus `send`.

```python
# messaging/base.py  (sketch — see coding-standards.md for full conventions)
@dataclass
class IncomingMessage:
    sender_id: str
    text: str
    timestamp: datetime
    raw: dict            # provider payload, for debugging only

class Messenger(Protocol):
    def send(self, text: str) -> None: ...
    def run(self, handler: Callable[[IncomingMessage], None]) -> None: ...  # blocks
```

- `TelegramMessenger`: raw Bot API via `httpx`, synchronous, long-polling
  (`getUpdates` loop). Persists the update `offset` (in SQLite) so restarts
  resume cleanly. Normalizes each update into `IncomingMessage`.
- `FakeMessenger` (tests): records sent text, lets tests inject incoming messages.
- A future `WhatsAppMessenger` only needs to satisfy `Messenger`. The core never
  imports the Telegram library.

## LLM abstraction

The hard part is not chat — it is **structured output**, which every provider
expresses differently (OpenAI `json_schema`, Gemini `response_schema`, Groq's
variant). The abstraction's job is to hide that behind one contract.

```python
# llm/base.py (sketch)
class LLMClient(Protocol):
    def complete(self, messages: list[dict], *,
                 schema: dict | None = None,
                 tools: list[dict] | None = None) -> LLMResponse: ...
```

- `schema` set  -> provider returns JSON validated against it -> `LLMResponse.data`.
- `tools` set   -> provider may return a tool call -> `LLMResponse.tool_call`.
- Two-tier usage: a **cheap** client (start: Gemini Flash-Lite) on the hot path;
  a **strong** client (start: Gemini Pro) for the weekly run. Both are just
  `LLMClient` instances; swapping providers = new adapter class.
- `FakeLLM` (tests): returns canned/scripted responses; never hits the network.

## Storage schema (SQLite, stdlib `sqlite3`, no ORM)

Single file. Tags stored as a JSON array string so v1 is free-form but v2 can
query with `json_each` without a migration (see ADR-002).

```sql
CREATE TABLE meals (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,     -- ISO8601, when the meal was eaten
    logged_at   TEXT NOT NULL,     -- when it was recorded
    raw_text    TEXT NOT NULL,     -- original user description(s)
    notes       TEXT
);

CREATE TABLE meal_items (
    id         INTEGER PRIMARY KEY,
    meal_id    INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,      -- "white bread", "chicken breast"
    quantity_g REAL,               -- model ESTIMATE; not authoritative
    calories   REAL,
    protein_g  REAL,
    fat_g      REAL,
    carbs_g    REAL,
    tags       TEXT                -- JSON array: '["soy","iron-rich"]'
);

CREATE TABLE symptoms (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    logged_at   TEXT NOT NULL,
    type        TEXT NOT NULL,     -- "bloating", "headache", ...
    severity    INTEGER,           -- optional 1-5
    duration    TEXT,              -- optional free text, e.g. "all morning"
    raw_text    TEXT NOT NULL,
    tags        TEXT               -- JSON array
);

CREATE TABLE pending (             -- hot-path session state, persisted
    chat_id     TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,     -- "meal" | "symptom"
    draft       TEXT NOT NULL,     -- JSON accumulated so far
    updated_at  TEXT NOT NULL
);

CREATE TABLE runs (                -- weekly-run idempotency / catch-up
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,     -- "weekly"
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL      -- "running" | "ok" | "error"
);

CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT);  -- e.g. telegram update offset
```

Numbers are **estimates** by design (ADR-003). `raw_text` is always kept so the
weekly run and future re-processing can re-derive structure if needed.

## Memory files (markdown)

- `data/diet.md` — fully agent-managed profile. The agent reads it, fuses
  old + new, rewrites. **Before every rewrite it snapshots the previous version**
  to `data/diet.history/diet-<timestamp>.md` (drift / lossy-rewrite safeguard,
  ADR-008).
- `data/observations.md` — agent-authored long-term memory, rewritten/appended
  by the weekly run. Human-readable so the author can correct it.

## Scheduling

APScheduler `BackgroundScheduler` runs inside the bot process (ADR-006). One
artifact, identical on Docker and the VM. `WEEKLY_CRON` checks daily (default
17:00 local time) whether 7 days have passed since the last successful review;
overridable via config. The same `run_weekly()` is also **triggerable on demand**
(a `/weekly` command) for testing and ad-hoc runs. Robustness comes from:
- Docker `restart: always` / systemd supervision (needed for the bot anyway).
- Idempotent weekly run keyed on the `runs` table (an on-demand run and the
  scheduled run for the same slot must not double-fire).
- No immediate startup catch-up (ADR-013): if a review is overdue when the
  process restarts, it simply waits for the next `WEEKLY_CRON` tick — which
  `CronTrigger` computes as today or tomorrow from the moment it starts — rather
  than firing at whatever odd hour the restart happened to land on.

Can be split to system cron later with zero change to `run_weekly()` — only the
trigger differs.

## Configuration & secrets

- All secrets via environment variables (`.env` in dev, never committed; real env
  on the VM). Keys: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, LLM API keys, model
  names per tier, `DATA_DIR`, schedule cron expression.
- `config.py` loads and validates these once at startup and fails loudly if a
  required one is missing.

## Deployment

- **Dev:** Docker Compose, single service, volume-mounted `data/` so the SQLite
  file and markdown survive restarts. Long-polling means no public URL needed.
- **Prod:** same image on a GCP VM (e2-micro/small), `restart: always`. The VM is
  ~90% of the monthly cost; the LLM usage is noise.

## Cost model (order of magnitude; verify live prices at build time)

Assumptions: ~20 messages/day, ~2 cheap calls/message; weekly run reads ~4 weeks
(~80k input tokens).

| Component | Rough monthly |
|---|---|
| Hot path (cheap model, ~1,200 calls/mo) | ~$0.30–0.60 |
| Weekly run (strong model, ~4.3 runs/mo) | ~$0.40–0.80 |
| **LLM total** | **~$1–2** |
| GCP VM (always-on) | ~$6–15 (or free-tier e2-micro) |

The only thing that could blow this up is feeding *full* history to the weekly
run instead of the compressed window — which the `observations.md` design exists
to prevent.
