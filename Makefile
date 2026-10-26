# Thin task runner for xirtun. Local targets run anywhere; the VM ops targets
# (logs/restart/status/stop/start) are meant to be run while SSH'd into the VM.
# `make` or `make help` lists everything.

.DEFAULT_GOAL := help
.PHONY: help dev weekly test lint check adduser removeuser logs restart status stop start

help:  ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

# One person's settings and data when WHO=<name> is given; otherwise the plain .env.
PERSON = $(if $(filter-out *,$(WHO)),ENV_FILE=.env.$(WHO) DATA_DIR=data/$(WHO))

# --- local dev ---
dev:  ## Run the bot (Telegram long-poll + weekly scheduler); WHO=<name> for one person
	$(PERSON) uv run python -m xirtun.main

weekly:  ## Run the weekly review once, now; WHO=<name> on the VM
	$(PERSON) uv run python -m xirtun.run_weekly

test:  ## Run the test suite
	uv run pytest

lint:  ## Lint with ruff
	uv run ruff check

check: lint test  ## Lint then test (what CI runs)

adduser:  ## Add a person: walks through their bot, chat id, settings and starts it
	uv run python -m xirtun.adduser

removeuser:  ## Remove a person: stops their bot and deletes their settings and data
	uv run python -m xirtun.removeuser

# --- VM ops (run while SSH'd into the VM) ---
# One service instance per person (xirtun@uros, ...). These act on all of them;
# narrow to one with WHO, e.g. `make logs WHO=uros`.
WHO ?= *
UNIT = 'xirtun@$(WHO)'

logs:  ## Tail the service logs (journalctl); WHO=<name> for one person
	sudo journalctl -u $(UNIT) -f

restart:  ## Restart the service(s)
	sudo systemctl restart $(UNIT)

status:  ## Show service status
	sudo systemctl status $(UNIT)

stop:  ## Stop the service(s)
	sudo systemctl stop $(UNIT)

start:  ## Start the service(s)
	sudo systemctl start $(UNIT)
