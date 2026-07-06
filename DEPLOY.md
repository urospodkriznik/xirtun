# Deploying xirtun to a GCP `e2-micro`

xirtun is a single long-running process: it long-polls Telegram and runs the
weekly review in-process via APScheduler. That makes deployment simple — no web
server, **no inbound ports, and no firewall rules** (long-polling is all
*outbound* HTTPS). We run it under `systemd` (no Docker — the overhead isn't worth
it on a 1 GB `e2-micro`).

This guide assumes a fresh Debian/Ubuntu VM and a deploy user named `xirtun` with
the repo at `/home/xirtun/xirtun`. Adjust paths if you use a different user/layout;
keep them in sync with [`deploy/xirtun.service`](deploy/xirtun.service).

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

## 3. Install prerequisites + uv

```bash
sudo apt-get update && sudo apt-get install -y git curl
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

## 5. Configure secrets (`.env`)

`.env` lives **only on the VM** — it is gitignored and CD never touches it.

```bash
cp .env.example .env
nano .env      # fill in the real values
```

Fill in:

| Variable | Notes |
|---|---|
| `TELEGRAM_TOKEN` | From @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat id |
| `GEMINI_API_KEY` | Google AI Studio key |
| `LLM_CHEAP_MODEL` | `gemini-2.5-flash` (flash-lite has been returning 503s) |
| `LLM_STRONG_MODEL` | `gemini-2.5-pro` |
| `WEEKLY_CRON` | Optional; default Sunday 09:00 (local). |
| `DATA_DIR` | Optional; defaults to `./data`. |

The app validates these at startup and fails loudly if a required one is missing.

Timezone isn't set here — the VM clock is UTC and the app defaults to it until the
user gives their timezone during the onboarding interview, after which it's stored
in the DB and used for meal/symptom times, the weekly cron, and the weight reminder.

### Data directory

SQLite data is created at runtime under `DATA_DIR` (default `data/`, gitignored).
It holds everything stateful:

- `xirtun.db` — the SQLite diary (meals, symptoms, known foods, sessions, offsets)
- `diet.md` — the agent-managed profile
- `observations.md` — the weekly run's long-term memory
- `diet.history/` — pre-rewrite profile snapshots

Nothing here is in git, so **don't `git clean -x` or delete `data/`**. Back it up with
the `/export` command (sends a JSON dump of the diary) or by copying `data/`.

## 6. Install and start the service

Back as a sudo-capable user (or with `sudo` from the `xirtun` shell):

```bash
sudo cp /home/xirtun/xirtun/deploy/xirtun.service /etc/systemd/system/xirtun.service
sudo systemctl daemon-reload
sudo systemctl enable --now xirtun     # start now + on every boot
sudo systemctl status xirtun           # should show "active (running)"
```

`Restart=always` keeps it up across crashes; `enable` brings it back after a reboot.
Send your bot a message to confirm it responds.

## 7. Logs

The service logs to the systemd journal (stdout/stderr):

```bash
sudo journalctl -u xirtun -f            # live tail
sudo journalctl -u xirtun -n 200        # last 200 lines
sudo journalctl -u xirtun --since today
```

The repo's `Makefile` wraps the common VM ops so you don't have to remember them:
`make logs`, `make restart`, `make status`, `make stop`, `make start` (run from
`~/xirtun` on the VM). `make` lists everything.

## 8. Allow CD to restart the service (passwordless sudo)

The deploy workflow restarts the service over SSH, so the deploy user needs
`NOPASSWD` sudo **scoped to exactly that one command** — nothing broader:

```bash
echo 'xirtun ALL=(root) NOPASSWD: /usr/bin/systemctl restart xirtun' \
  | sudo tee /etc/sudoers.d/xirtun-deploy
sudo chmod 0440 /etc/sudoers.d/xirtun-deploy
sudo visudo -c                          # validate sudoers syntax
```

(Confirm the `systemctl` path with `which systemctl` — it's `/usr/bin/systemctl` on
Debian/Ubuntu.)

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
sudo systemctl restart xirtun
```

## Schema changes

Today the DB uses `CREATE TABLE IF NOT EXISTS` plus a couple of additive
`ALTER TABLE` migrations (`storage/db.py`), which apply automatically on startup.
Anything beyond additive (renames, type changes, backfills) needs a real migration
step added to the deploy before that release ships.
