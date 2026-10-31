# Deploying xirtun to a GCP `e2-micro`

xirtun is a long-running process: it long-polls Telegram and runs the weekly
review in-process via APScheduler. Each person who uses it gets **their own
instance** — the same code, but their own Telegram bot, their own settings file and
their own data folder ([ADR-017](docs/decisions.md)). That keeps deployment simple —
no web server, **no inbound ports, and no firewall rules** (long-polling is all
*outbound* HTTPS). We run the instances under `systemd` (no Docker — the overhead
isn't worth it on a 1 GB `e2-micro`).

This guide assumes a fresh Debian/Ubuntu VM and a deploy user named `xirtun` with
the repo at `/home/xirtun/xirtun`. Adjust paths if you use a different user/layout;
keep them in sync with [`deploy/xirtun@.service`](deploy/xirtun@.service). The
examples use `uros` as the instance name; use one short lowercase name per person.

> The CI/CD workflows in [`.github/workflows/`](.github/workflows) automate the
> *update* step (push to `main` → SSH in → pull + restart). Everything below is the
> one-time **bootstrap** that those workflows assume is already done.

---

## 1. Create the VM

In the GCP console (or `gcloud`), create an `e2-micro` instance:

- **Machine type:** `e2-micro` (in an eligible region — `us-west1`, `us-central1`,
  `us-east1` — this falls under the GCP always-free tier).
- **Boot disk:** Debian 12 or Ubuntu 24.04, default size is fine.
- **Firewall:** leave everything closed. xirtun needs **no inbound ports** — Telegram
  long-polling and the Gemini API are both outbound HTTPS.

> ⚠️ Creating/running a VM can incur GCP cost outside the free tier. Confirm the
> region/machine type before launching.

SSH in (the console "SSH" button, or `gcloud compute ssh ...`).

## 2. Create the deploy user

```bash
sudo adduser --disabled-password --gecos "" xirtun
sudo install -d -o xirtun -g xirtun /home/xirtun   # usually already exists
sudo -iu xirtun                                     # become the deploy user
```

The rest of the bootstrap runs **as the `xirtun` user** unless a command says `sudo`.

A dedicated user is optional. You can just as well run everything as your own login
user, with the repo in your home — what matters is that **one user** owns the
checkout, runs the service, is `SSH_USER` for CD, and runs `make adduser`. Step 6
installs the unit for whichever user that is.

## 3. Install prerequisites + uv

```bash
sudo apt-get update && sudo apt-get install -y git curl make
curl -LsSf https://astral.sh/uv/install.sh | sh      # installs uv to ~/.local/bin
source ~/.bashrc                                      # put uv on PATH for this shell
uv --version
```

uv manages the Python toolchain itself, so you don't need to install Python
separately. The systemd unit calls uv by absolute path (`~/.local/bin/uv`), since
systemd doesn't load the login PATH.

## 4. Clone and sync

```bash
cd ~
git clone https://github.com/urospodkriznik/xirtun.git xirtun
cd xirtun
uv sync
```

## 5. Configure settings (`.env` shared, `.env.<name>` per person)

Settings come in two layers, both living **only on the VM** (gitignored; CD never
touches them):

- **`.env`** — the shared defaults for everyone: models, schedules, and usually the
  Gemini key.
- **`.env.<name>`** — one per person, holding what is theirs: their bot's token and
  their chat id, plus anything they want different (their own Gemini key, another
  schedule). A value here overrides the same one in `.env`.

`make adduser` (step 6) writes the per-person files for you. The shared one you fill
in once:

```bash
cp .env.example .env
nano .env      # fill in the shared values; leave the TELEGRAM_* lines empty
```

| Variable | Where | Notes |
|---|---|---|
| `TELEGRAM_TOKEN` | `.env.<name>` only | This person's own bot, from @BotFather |
| `TELEGRAM_CHAT_ID` | `.env.<name>` only | This person's chat id |
| `GEMINI_API_KEY` | either | Google AI Studio key; per person only if they pay for their own |
| `LLM_CHEAP_MODEL` | either | `gemini-2.5-flash` (flash-lite has been returning 503s) |
| `LLM_STRONG_MODEL` | either | `gemini-2.5-pro` |
| `WEEKLY_CRON` | either | Optional; default daily 17:00 (local), runs if a week has passed. |
| `WEIGHT_REMINDER_CRON` | either | Optional; default daily 08:00 (local). |

The Telegram keys are never taken from `.env`: an instance whose own file lacks them
refuses to start rather than borrowing someone else's bot. Leave `DATA_DIR` out of
both — the service sets it to `data/<name>/` itself, so two instances can never point
at the same folder. Every person needs **their own bot**: Telegram hands a bot's
messages to one reader at a time, and each bot only answers the chat id in its own
file.

The app validates all this at startup and fails loudly if a required value is missing.

Timezone isn't set here — the VM clock is UTC and the app defaults to it until the
user gives their timezone during the onboarding interview, after which it's stored
in the DB and used for meal/symptom times, the weekly cron, and the weight reminder.

### Data directory

Each instance's data is created at runtime under `data/<name>/` (gitignored). It
holds everything stateful for that person — nothing is shared between instances:

- `xirtun.db` — the SQLite diary (meals, symptoms, known foods, sessions, offsets)
- `diet.md` — the agent-managed profile
- `observations.md` — the weekly run's long-term memory
- `diet.history/` — pre-rewrite profile snapshots

A person can erase all of theirs with `/cleardata` (it isn't in the bot's menu or
`/help`, on purpose): every table, their profile and notes, then a `VACUUM` so the
rows are really gone. It never touches anyone else's folder.

Nothing here is in git, so **don't `git clean -x` or delete `data/`**. Each person can
back up their own with
the `/exportbackup` command — it sends one JSON file holding the diary, your metrics
and targets, weight and waist logs, saved foods, past weekly reviews, and the memory
files including their history snapshots — or by copying `data/` wholesale. If you copy
the database by hand, take `xirtun.db-wal` with it: recent writes may still be sitting
in the write-ahead log and copying `xirtun.db` alone can silently miss them.

## 6. Install the service, then add people

Back as a sudo-capable user (or with `sudo` from the `xirtun` shell), install the
unit and the sudo rule once:

```bash
RUN_AS=xirtun            # the user that owns the checkout (your login user, if you use that)
cd /home/$RUN_AS/xirtun
sed "s#/home/xirtun/#/home/$RUN_AS/#g; s#^User=xirtun#User=$RUN_AS#; s#^Group=xirtun#Group=$RUN_AS#" \
  deploy/xirtun@.service | sudo tee /etc/systemd/system/xirtun@.service > /dev/null
sudo systemctl daemon-reload
echo "$RUN_AS ALL=(root) NOPASSWD: /usr/bin/systemctl restart xirtun@*, /usr/bin/systemctl enable --now xirtun@*, /usr/bin/systemctl disable --now xirtun@*" \
  | sudo tee /etc/sudoers.d/xirtun-deploy
sudo chmod 0440 /etc/sudoers.d/xirtun-deploy
sudo visudo -c                          # validate sudoers syntax
```

The `sed` fills in your user and paths as it copies, so the repo's own copy of the unit
is never edited. Keep it that way: **don't edit tracked files on the VM** — a local
change makes every later `git pull` refuse to run, and CD with it. If a release ever
changes `deploy/xirtun@.service`, rerun these lines; CD doesn't touch `/etc`.

The unit is a systemd *template*: one file, and the part after `@` picks the person.
`xirtun@uros` reads `.env.uros` (over the shared `.env`) and keeps its data in
`data/uros/`. The sudo rule is explained in step 8.

### Adding a person — `make adduser`

As the user that owns the checkout (`xirtun` in this guide), from `~/xirtun`:

```bash
make adduser
```

It walks you through everything, and you need nothing open but Telegram:

1. **A name** for their files (`ana`) — checked to be lowercase, not already set up,
   and not clashing with an existing data folder.
2. **Their bot.** It shows the four BotFather steps (`/newbot`, a display name, a
   username ending in `bot`, copy the token). Paste the token; it's checked with
   Telegram and against every other person's file, since bots can't be shared.
3. **Their chat id**, found for you: they open the new bot and press Start, and the
   script picks their chat up — no @userinfobot needed. (Ctrl-C to type one instead.)
4. **A Gemini key** — Enter to use the shared one.
5. It writes `.env.<name>` (readable only by you) and runs
   `systemctl enable --now xirtun@<name>`.

Their bot then answers the message they sent in step 3 with the onboarding interview.

### Removing a person — `make removeuser`

Also as the checkout's owner. It asks for the name, lists exactly what it will delete,
and makes you type the name again. Then it stops and disables `xirtun@<name>` (first,
so a running bot can't recreate files mid-delete) and deletes `.env.<name>` and
`data/<name>/`. Nobody else's files are touched, and the shared `.env` stays.

What lives outside those files it can't delete, so it lists it at the end: the bot
itself (`/deletebot` in @BotFather — it names the bot for you), the person's Telegram
chat, the system journal (which can only be cleared for everyone at once), and
whatever the model provider retains. `/cleardata`, by contrast, erases someone's data
but keeps their bot running for a fresh start.
Making the bot itself is the one step that can't be scripted: Telegram only creates
bots through a chat with @BotFather. `Restart=always` keeps every instance up across
crashes; `enable` brings it back after a reboot. CD picks new instances up on its own.

## 7. Logs

The service logs to the systemd journal (stdout/stderr):

```bash
sudo journalctl -u 'xirtun@*' -f          # live tail, everyone
sudo journalctl -u xirtun@uros -n 200     # last 200 lines, one person
sudo journalctl -u xirtun@uros --since today
```

The repo's `Makefile` wraps the common VM ops so you don't have to remember them:
`make logs`, `make restart`, `make status`, `make stop`, `make start` (run from
`~/xirtun` on the VM). They act on every instance; add `WHO=<name>` for just one
(`make logs WHO=uros`). `make` lists everything.

To run one person's weekly review by hand: `make weekly WHO=uros` (it points the runner
at `.env.uros` and `data/uros/`; without `WHO` it would read only the shared `.env`,
which has no bot to send from).

## 8. Allow CD to restart the service (passwordless sudo)

The rule installed in step 6 gives the deploy user `NOPASSWD` sudo for exactly three
commands: restarting the instances (what the deploy workflow does over SSH,
`systemctl restart 'xirtun@*'`), enabling a new one (`make adduser`) and disabling one
(`make removeuser`).

(Confirm the `systemctl` path with `which systemctl` — it's `/usr/bin/systemctl` on
Debian/Ubuntu.) The `*` means new instances need no change here. sudo's `*` also
matches spaces, so this lets the deploy user restart, enable or disable — nothing
more — other units too, as long as the command starts with a `xirtun@` one; an accepted trade for not
editing sudoers every time someone is added.

## 9. SSH key for CD

Generate a dedicated deploy keypair and authorize it for the `xirtun` user:

```bash
# on the VM (or locally, then copy the public key up):
ssh-keygen -t ed25519 -f ~/xirtun_deploy -N ""
cat ~/xirtun_deploy.pub >> /home/xirtun/.ssh/authorized_keys
```

Then add these as **GitHub Actions repository secrets** (Settings → Secrets and
variables → Actions):

| Secret | Value |
|---|---|
| `SSH_HOST` | the VM's external IP |
| `SSH_USER` | `xirtun` |
| `SSH_KEY` | the **private** key (contents of `~/xirtun_deploy`) |

Keep the private key off the VM afterwards. See
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) for how they're used.

---

## Updating manually

CD handles this automatically on push to `main`, but to do it by hand:

```bash
cd /home/xirtun/xirtun
git pull --ff-only
uv sync
sudo systemctl restart 'xirtun@*'
```

## Moving from the single-instance setup

Before ADR-017 there was one `xirtun` service, reading `.env` and keeping its data
straight in `data/`. To turn that into the first person's instance (here `uros`),
without losing anything (if CD deployed this change first, its restart step failed
on the old sudo rule — expected, and the old service kept running until now):

**a) As your sudo-capable login user** — stop the old service so nothing writes
mid-move:

```bash
sudo systemctl disable --now xirtun
```

**b) As the user that owns the checkout** (`sudo -iu xirtun` in this guide's layout;
if the old service ran as your login user, just stay as yourself) — so the moved files
keep the owner the service runs as:

```bash
cd ~/xirtun
git pull --ff-only && ~/.local/bin/uv sync   # if it refuses over deploy/xirtun.service, see below
mv data data-uros && mkdir data && mv data-uros data/uros    # moves the -wal/-shm too
grep '^TELEGRAM_' .env > .env.uros && chmod 600 .env.uros   # your bot -> your file
sed -i '/^TELEGRAM_/d; /^DATA_DIR/d' .env && chmod 600 .env  # .env keeps the shared rest
cat .env.uros                     # expect exactly TELEGRAM_TOKEN=… and TELEGRAM_CHAT_ID=…
exit
```

**c) Back as your login user** — remove the old unit, install the template and the
sudo rule exactly as in step 6 (with `RUN_AS` set to the checkout's owner), then start
your instance:

```bash
sudo rm /etc/systemd/system/xirtun.service
# ...the step 6 block...
sudo systemctl enable --now xirtun@uros
sudo journalctl -u xirtun@uros --no-pager | grep 'scheduler started'
```

The last line should end in your own timezone, not `UTC` — proof the instance found
the database you moved into `data/uros/`.

If `git pull` refused because `deploy/xirtun.service` has local changes, that file was
edited on the VM to fill in the user and paths. Check `git diff deploy/xirtun.service`
shows only those, then discard it with `git checkout -- deploy/xirtun.service` and pull
again — step 6's `sed` now does that job outside the repo.

(`make adduser` isn't used for you: it refuses a name whose data folder already holds
data, which is exactly the point here.) Send the bot a message: it should carry on
exactly where it was (same diary, same profile). Then run `make adduser` for the next
person.

## Schema changes

Today the DB uses `CREATE TABLE IF NOT EXISTS` plus a couple of additive
`ALTER TABLE` migrations (`storage/db.py`), which apply automatically on startup.
Anything beyond additive (renames, type changes, backfills) needs a real migration
step added to the deploy before that release ships.
