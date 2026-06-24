# Thin task runner for xirtun. Local targets run anywhere; the VM ops targets
# (logs/restart/status/stop/start) are meant to be run while SSH'd into the VM.
# `make` or `make help` lists everything.

.DEFAULT_GOAL := help
.PHONY: help dev weekly test lint check logs restart status stop start

help:  ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

# --- local dev ---
dev:  ## Run the bot (Telegram long-poll + weekly scheduler)
	uv run python -m xirtun.main

weekly:  ## Run the weekly review once, now
	uv run python -m xirtun.run_weekly

test:  ## Run the test suite
	uv run pytest

lint:  ## Lint with ruff
	uv run ruff check

check: lint test  ## Lint then test (what CI runs)

# --- VM ops (run while SSH'd into the VM) ---
logs:  ## Tail the service logs (journalctl)
	sudo journalctl -u xirtun -f

restart:  ## Restart the service
	sudo systemctl restart xirtun

status:  ## Show service status
	sudo systemctl status xirtun

stop:  ## Stop the service
	sudo systemctl stop xirtun

start:  ## Start the service
	sudo systemctl start xirtun
