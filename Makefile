SHELL := /bin/bash
VENV := .venv
BIN := $(VENV)/bin
AUTO_TMS := $(BIN)/auto_tms

.PHONY: setup config run status log stop reset help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-14s %s\n", $$1, $$2}'

setup: ## Install dependencies and playwright
	uv venv && uv pip install -e .
	$(BIN)/playwright install --with-deps chromium

config: ## Configure credentials, host, and proxy
	@echo "Setting up .env (will not be committed to git)"
	@echo ""
	@if [ -f .env ]; then echo "Current .env:"; cat .env; echo ""; fi
	@read -p "TMS_USER (員工編號): " user; \
	 read -sp "TMS_PASSWD: " passwd; echo ""; \
	 read -p "ANTHROPIC_API_KEY: " apikey; \
	 read -p "TMS_HOST: " host; \
	 read -p "TMS_PROXY (e.g. socks5://127.0.0.1:1080) [none]: " proxy; \
	 echo "TMS_USER=$$user" > .env; \
	 echo "TMS_PASSWD=$$passwd" >> .env; \
	 echo "ANTHROPIC_API_KEY=$$apikey" >> .env; \
	 echo "TMS_HOST=$$host" >> .env; \
	 if [ -n "$$proxy" ]; then echo "TMS_PROXY=$$proxy" >> .env; fi; \
	 echo ""; echo "Saved to .env"

run: ## Full pipeline (or: make run ID=198761)
ifdef ID
	$(AUTO_TMS) -v run $(ID)
else
	nohup $(AUTO_TMS) -v run > ~/.auto_tms/logs/run_output.log 2>&1 & echo "PID: $$!"
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
	@rm -f ~/.auto_tms/state/progress.json ~/.auto_tms/state/plan.json
	@echo "Cleared plan and progress"
