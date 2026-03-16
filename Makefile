SHELL := /bin/bash
VENV := .venv
BIN := $(VENV)/bin
AUTO_TMS := $(BIN)/auto_tms

.PHONY: setup config status plan run complete log help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-12s %s\n", $$1, $$2}'

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

status: ## Show 我的學程 completion status (cached)
	@$(AUTO_TMS) status

plan: ## Scrape live data and build course plan
	$(AUTO_TMS) -v plan

run: ## Full pipeline: plan → complete → verify
	nohup $(AUTO_TMS) -v run > ~/.auto_tms/logs/run_output.log 2>&1 & echo "PID: $$!"

complete: ## Complete one course: make complete ID=198761
	$(AUTO_TMS) -v complete $(ID)

log: ## Tail the pipeline log
	@tail -f ~/.auto_tms/logs/run_output.log

progress: ## Show pipeline progress summary
	@$(BIN)/python3 -m auto_tms.progress

stop: ## Stop running pipeline
	@pkill -f "auto_tms.*run" 2>/dev/null && echo "Stopped" || echo "Not running"
