# auto_tms — 教育訓練系統自動化測試工具

自動完成教育訓練管理系統 (TMS) 的線上課程：影片播放、教材閱讀、問卷填寫、測驗作答。

## 功能

- **自動登入** — CAPTCHA 辨識（支援 LLM Vision 或 ddddocr 離線辨識）
- **智慧規劃** — 合併「待修課程」清單 + 學程時數缺口分析，統一規劃最少修課清單
- **課程完成** — 自動處理影片、教材、問卷、測驗
- **測驗作答** — LLM 輔助作答（可選）或純暴力法 + 分數搜尋（不需 API key）
- **斷點續傳** — 逐項紀錄進度，中斷後從上次狀態繼續
- **全自動流程** — 規劃 → 完成 → 驗證，最多重試 3 輪直到所有學程通過

## 安裝

需要 Python 3.11+ 和 [uv](https://github.com/astral-sh/uv)。

```bash
git clone <repo-url> && cd auto_tms
make setup        # 建立 venv、安裝套件、下載 Chromium
make config       # 互動式設定（帳號、主機、LLM provider）
```

## 設定

`make config` 會引導設定並產生 `.env`（已加入 `.gitignore`）。

### LLM 模式選擇

| 模式 | CAPTCHA | 測驗 | 需要 |
|------|---------|------|------|
| **不使用 LLM**（預設） | ddddocr 離線辨識 | 隨機答案 + 分數搜尋 | `make setup` 已包含 |
| Anthropic Claude | Haiku Vision | Sonnet | `ANTHROPIC_API_KEY` |
| OpenAI | GPT-4o-mini Vision | GPT-4o-mini | `TMS_LLM_API_KEY` |
| Google Gemini | Gemini Flash Vision | Gemini Flash | `TMS_LLM_API_KEY`（有免費額度）|
| 本地模型 | ddddocr | Ollama/vLLM 等 | `TMS_LLM_BASE_URL` |

不使用 LLM 時完全不需要 API key，測驗透過逐題分數搜尋（約 5-15 次嘗試）自動通過。使用 LLM 可減少測驗嘗試次數。

### 環境變數

| 變數 | 說明 | 必填 |
|------|------|------|
| `TMS_USER` | 員工編號 | 是 |
| `TMS_PASSWD` | 密碼 | 是 |
| `TMS_HOST` | TMS 主機 | 是 |
| `TMS_PROXY` | 代理伺服器，如 `socks5://127.0.0.1:1080` | 否 |
| `TMS_MAX_PAGES` | 同時開啟頁面上限（預設 5） | 否 |
| `TMS_MAX_VIDEOS` | 同時播放影片上限（預設 2） | 否 |
| `TMS_LLM_PROVIDER` | `none` / `anthropic` / `openai` / `gemini` / `local`（預設 `none`）| 否 |
| `ANTHROPIC_API_KEY` | Anthropic API key（provider=anthropic） | 否 |
| `TMS_LLM_API_KEY` | OpenAI / Gemini API key | 否 |
| `TMS_LLM_BASE_URL` | 本地模型 API URL（provider=local） | 否 |
| `TMS_LLM_MODEL` | 覆蓋預設模型名稱 | 否 |

## 使用

```bash
make run                         # 全自動（待修課程 + 學程規劃）
make run MODE=pending            # 僅完成待修課程
make run MODE=program            # 僅學程規劃選課
make run ID=198761               # 完成單一課程
make status                      # 即時狀態（scrape 網站 + 本地進度）
make status CACHED=1             # 快取狀態（不連網）
make status ALL=1                # 顯示全部學程與課程
make log                         # 查看即時 log
make stop                        # 停止執行中的流程
make reset                       # 清除進度，重新開始
```

也可以直接使用 CLI：

```bash
auto_tms run                    # 全自動（待修 + 學程）
auto_tms run --mode pending     # 僅完成待修課程
auto_tms run --mode program     # 僅學程規劃選課
auto_tms run <courseId>         # 完成單一課程
auto_tms run -f courses.txt    # 從檔案批次完成
auto_tms status                 # 即時狀態
auto_tms status --cached        # 快取狀態
auto_tms status --all           # 顯示全部（含已通過/已完成）
auto_tms log                    # 查看 log
auto_tms -v <command>           # 顯示詳細 log
```

## 資料目錄

所有狀態存放於 `~/.auto_tms/`：

```
~/.auto_tms/
├── session/       # Playwright 瀏覽器 session
├── state/         # 進度追蹤 (run.json, progress/, plan.json, exam_memory/)
└── logs/          # log 檔
```

## 技術架構

- **Playwright** — 無頭瀏覽器自動化
- **LLM（可選）** — CAPTCHA 辨識、測驗作答（支援 Anthropic / OpenAI / Gemini / 本地模型）
- **ddddocr** — 離線 CAPTCHA 辨識（不使用 LLM 時）
- **Pydantic** — 狀態模型與 JSON 持久化
- **Click** — CLI 框架
- **asyncio** — 並行處理多門課程

## 作者

Yen-Ju Chu (mantour.tw@gmail.com)

## 授權

本專案採用 [MIT License](LICENSE) 授權。

本工具僅供測試與研究用途。使用者應自行確認符合所屬機構之相關規範。
