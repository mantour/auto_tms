SHELL := /bin/bash
VENV := .venv
BIN := $(VENV)/bin
AUTO_TMS := $(BIN)/auto_tms

.PHONY: setup config run status log stop reset help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-14s %s\n", $$1, $$2}'

setup: ## Install dependencies and playwright
	uv venv && uv pip install -e ".[ocr]"
	$(BIN)/playwright install --with-deps chromium

config: ## Configure credentials, host, proxy, and LLM
	@bash scripts/config.sh

run: ## Full pipeline (ID=N for single, MODE=pending/program/all)
ifdef ID
	$(AUTO_TMS) -v run $(ID)
else
	nohup $(AUTO_TMS) -v run $(if $(MODE),--mode $(MODE),) > ~/.auto_tms/logs/run_output.log 2>&1 & echo "PID: $$!"
endif

status: ## Show status (add ALL=1 for full list, CACHED=1 for offline)
ifdef CACHED
	$(AUTO_TMS) status --cached $(if $(ALL),--all,)
else
	$(AUTO_TMS) status $(if $(ALL),--all,)
endif

log: ## Tail today's log
	@$(AUTO_TMS) log

stop: ## Stop running pipeline
	@pkill -f "auto_tms.*run" 2>/dev/null && echo "Stopped" || echo "Not running"

reset: ## Clear plan and progress (start fresh)
	@rm -f ~/.auto_tms/state/run.json ~/.auto_tms/state/plan.json
	@rm -rf ~/.auto_tms/state/progress/
	@echo "Cleared plan and progress"
