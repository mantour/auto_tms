#!/usr/bin/env bash
# Interactive .env configuration with defaults from existing values.
# Usage: bash scripts/config.sh

set -euo pipefail

ENV_FILE=".env"

# Read current value from .env
get_cur() { grep "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || echo ""; }

# Prompt with default. Usage: ask VAR "prompt" [default_override]
# If .env has a value, show it as default. Enter keeps it.
ask() {
    local var="$1" prompt="$2" default="${3:-}"
    local cur; cur=$(get_cur "$var")
    cur="${cur:-$default}"
    local display="$cur"
    read -rp "$prompt [$display]: " val
    eval "$var=\"\${val:-\$cur}\""
}

# Prompt for password (masked)
ask_secret() {
    local var="$1" prompt="$2"
    local cur; cur=$(get_cur "$var")
    local hint=""
    [ -n "$cur" ] && hint="****"
    read -rsp "$prompt [$hint]: " val
    echo ""
    eval "$var=\"\${val:-\$cur}\""
}

echo "Setting up .env (will not be committed to git)"
echo ""
if [ -f "$ENV_FILE" ]; then echo "Current .env:"; cat "$ENV_FILE"; echo ""; fi

# Basic settings
ask TMS_USER "TMS_USER (員工編號)"
ask_secret TMS_PASSWD "TMS_PASSWD"
ask TMS_HOST "TMS_HOST"
ask TMS_PROXY "TMS_PROXY (e.g. socks5://127.0.0.1:1080)"
ask TMS_MAX_PAGES "TMS_MAX_PAGES (concurrent pages)" "5"
ask TMS_MAX_VIDEOS "TMS_MAX_VIDEOS (concurrent videos)" "2"

# LLM settings
echo ""
echo "LLM 設定 (用於驗證碼辨識和測驗作答):"
echo "  1. 不使用 LLM (驗證碼用 ddddocr, 測驗用暴力法)"
echo "  2. Anthropic Claude API key"
echo "  3. OpenAI API key (按量計費)"
echo "  4. Google Gemini API key (有免費額度)"
echo "  5. 本地模型 (Ollama/vLLM 等)"

cur_provider=$(get_cur TMS_LLM_PROVIDER)
cur_provider="${cur_provider:-none}"
case "$cur_provider" in
    anthropic) cur_choice=2 ;;
    openai)    cur_choice=3 ;;
    gemini)    cur_choice=4 ;;
    local)     cur_choice=5 ;;
    *)         cur_choice=1 ;;
esac

read -rp "選擇 [$cur_choice]: " llm_choice
llm_choice="${llm_choice:-$cur_choice}"

TMS_LLM_PROVIDER="none"
ANTHROPIC_API_KEY=""
TMS_LLM_API_KEY=""
TMS_LLM_BASE_URL=""
TMS_LLM_MODEL=""

case "$llm_choice" in
    2)
        TMS_LLM_PROVIDER="anthropic"
        ask_secret ANTHROPIC_API_KEY "ANTHROPIC_API_KEY"
        ;;
    3)
        TMS_LLM_PROVIDER="openai"
        ask_secret TMS_LLM_API_KEY "OpenAI API key"
        ;;
    4)
        TMS_LLM_PROVIDER="gemini"
        ask_secret TMS_LLM_API_KEY "Gemini API key"
        ;;
    5)
        TMS_LLM_PROVIDER="local"
        ask TMS_LLM_BASE_URL "API Base URL" "http://localhost:11434/v1"
        ask TMS_LLM_MODEL "Model name" "llama3"
        ask_secret TMS_LLM_API_KEY "API Key (不需要直接 Enter)"
        ;;
esac

# Write .env
{
    echo "TMS_USER=$TMS_USER"
    echo "TMS_PASSWD=$TMS_PASSWD"
    echo "TMS_HOST=$TMS_HOST"
    [ -n "$TMS_PROXY" ] && echo "TMS_PROXY=$TMS_PROXY"
    [ "$TMS_MAX_PAGES" != "5" ] && echo "TMS_MAX_PAGES=$TMS_MAX_PAGES"
    [ "$TMS_MAX_VIDEOS" != "2" ] && echo "TMS_MAX_VIDEOS=$TMS_MAX_VIDEOS"
    echo "TMS_LLM_PROVIDER=$TMS_LLM_PROVIDER"
    [ -n "$ANTHROPIC_API_KEY" ] && echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
    [ -n "$TMS_LLM_API_KEY" ] && echo "TMS_LLM_API_KEY=$TMS_LLM_API_KEY"
    [ -n "$TMS_LLM_BASE_URL" ] && echo "TMS_LLM_BASE_URL=$TMS_LLM_BASE_URL"
    [ -n "$TMS_LLM_MODEL" ] && echo "TMS_LLM_MODEL=$TMS_LLM_MODEL"
} > "$ENV_FILE"

echo ""
echo "Saved to $ENV_FILE"
