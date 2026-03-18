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
	@echo "Setting up .env (will not be committed to git)"
	@echo ""
	@if [ -f .env ]; then echo "Current .env:"; cat .env; echo ""; fi
	@read -p "TMS_USER (員工編號): " user; \
	 read -sp "TMS_PASSWD: " passwd; echo ""; \
	 read -p "TMS_HOST: " host; \
	 read -p "TMS_PROXY (e.g. socks5://127.0.0.1:1080) [none]: " proxy; \
	 read -p "TMS_MAX_PAGES (concurrent pages) [5]: " max_pages; \
	 read -p "TMS_MAX_VIDEOS (concurrent videos) [2]: " max_videos; \
	 echo ""; \
	 echo "LLM 設定 (用於驗證碼辨識和測驗作答):"; \
	 echo "  1. 不使用 LLM (驗證碼用 ddddocr, 測驗用暴力法)"; \
	 echo "  2. Anthropic Claude API key"; \
	 echo "  3. OpenAI API key (按量計費)"; \
	 echo "  4. Google Gemini API key (有免費額度)"; \
	 echo "  5. 本地模型 (Ollama/vLLM 等)"; \
	 read -p "選擇 [1]: " llm_choice; \
	 llm_provider="none"; llm_key=""; llm_base=""; llm_model=""; anthropic_key=""; \
	 if [ "$$llm_choice" = "2" ]; then \
	   llm_provider="anthropic"; \
	   read -p "ANTHROPIC_API_KEY: " anthropic_key; \
	 elif [ "$$llm_choice" = "3" ]; then \
	   llm_provider="openai"; \
	   read -p "OpenAI API key: " llm_key; \
	 elif [ "$$llm_choice" = "4" ]; then \
	   llm_provider="gemini"; \
	   read -p "Gemini API key: " llm_key; \
	 elif [ "$$llm_choice" = "5" ]; then \
	   llm_provider="local"; \
	   read -p "API Base URL [http://localhost:11434/v1]: " llm_base; \
	   read -p "Model name [llama3]: " llm_model; \
	   read -p "API Key (不需要直接 Enter) []: " llm_key; \
	 fi; \
	 echo "TMS_USER=$$user" > .env; \
	 echo "TMS_PASSWD=$$passwd" >> .env; \
	 echo "TMS_HOST=$$host" >> .env; \
	 if [ -n "$$proxy" ]; then echo "TMS_PROXY=$$proxy" >> .env; fi; \
	 if [ -n "$$max_pages" ]; then echo "TMS_MAX_PAGES=$$max_pages" >> .env; fi; \
	 if [ -n "$$max_videos" ]; then echo "TMS_MAX_VIDEOS=$$max_videos" >> .env; fi; \
	 echo "TMS_LLM_PROVIDER=$$llm_provider" >> .env; \
	 if [ -n "$$anthropic_key" ]; then echo "ANTHROPIC_API_KEY=$$anthropic_key" >> .env; fi; \
	 if [ -n "$$llm_key" ]; then echo "TMS_LLM_API_KEY=$$llm_key" >> .env; fi; \
	 if [ -n "$$llm_base" ]; then echo "TMS_LLM_BASE_URL=$$llm_base" >> .env; fi; \
	 if [ -n "$$llm_model" ]; then echo "TMS_LLM_MODEL=$$llm_model" >> .env; fi; \
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
	@rm -f ~/.auto_tms/state/run.json ~/.auto_tms/state/plan.json
	@rm -rf ~/.auto_tms/state/progress/
	@echo "Cleared plan and progress"
