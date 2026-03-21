# auto_tms — 教育訓練系統自動化測試工具

自動完成教育訓練管理系統 (TMS) 的線上課程：影片播放、教材閱讀、問卷填寫、測驗作答。
設定帳號後一鍵執行，不需要 API key，不需要手動操作。

## 快速開始

### 前置需求

- **Linux / macOS**：安裝 [uv](https://github.com/astral-sh/uv)（會自動安裝 Python）：
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Windows**：建議使用 [WSL](https://learn.microsoft.com/zh-tw/windows/wsl/install)（`wsl --install`），然後在 WSL 內安裝 uv。

### 安裝與執行

```bash
git clone https://github.com/mantour/auto_tms.git && cd auto_tms
make setup        # 安裝套件、下載瀏覽器
make config       # 依提示輸入帳號、密碼、主機位址
make run          # 開始自動完成課程（背景執行）
```

查看進度：

```bash
make status       # 查看學程完成度和課程進度
make log          # 查看即時 log
make stop         # 停止執行
```

## 常見問題

**需要 API key 嗎？**
不需要。預設使用離線模式（ddddocr 辨識驗證碼 + 自動搜尋測驗答案），完全免費。有 API key 可以加速測驗作答，見進階設定。

**測驗怎麼通過的？**
自動嘗試不同答案組合，根據分數變化逐題找出正確答案，通常 5-15 次嘗試內通過。

**中斷了怎麼辦？**
再次 `make run`，會從上次進度繼續，已完成的課程不會重做。

**哪些課程不能自動完成？**
面授課程無法線上完成，工具會自動跳過並選擇其他替代課程。

**想重新開始？**
`make reset` 清除所有進度，下次 `make run` 從頭開始。

## 使用

```bash
make run                         # 全自動（待修課程 + 學程規劃）
make run MODE=pending            # 僅完成待修課程
make run MODE=program            # 僅學程規劃選課
make run ID=198761               # 完成單一課程
make status                      # 查看狀態（快取）
make status-refresh              # 從網頁重新抓取並更新快取
make status-all                  # 顯示全部課程（含已完成）
make log                         # 查看即時 log
make stop                        # 停止執行中的流程
make reset                       # 清除進度，重新開始
```

<details>
<summary>CLI 指令</summary>

```bash
auto_tms run                    # 全自動（待修 + 學程）
auto_tms run --mode pending     # 僅完成待修課程
auto_tms run --mode program     # 僅學程規劃選課
auto_tms run <courseId>         # 完成單一課程
auto_tms run -f courses.txt    # 從檔案批次完成
auto_tms status                 # 查看狀態（快取）
auto_tms status --refresh       # 從網頁重新抓取
auto_tms status --all           # 顯示全部（含已完成課程）
auto_tms log                    # 查看 log
auto_tms -v <command>           # 顯示詳細 log
```

</details>

## 設定

`make config` 會引導設定並產生 `.env`（不會被 commit）。
必填項目只有三個：員工編號、密碼、TMS 主機位址。

<details>
<summary>LLM 模式選擇（可選，加速測驗）</summary>

| 模式 | CAPTCHA | 測驗 | 需要 |
|------|---------|------|------|
| **不使用 LLM**（預設） | ddddocr 離線辨識 | 隨機答案 + 分數搜尋 | `make setup` 已包含 |
| Anthropic Claude | Haiku Vision | Sonnet | `ANTHROPIC_API_KEY` |
| OpenAI | GPT-4o-mini Vision | GPT-4o-mini | `TMS_LLM_API_KEY` |
| Google Gemini | Gemini Flash Vision | Gemini Flash | `TMS_LLM_API_KEY`（有免費額度）|
| 本地模型 | ddddocr | Ollama/vLLM 等 | `TMS_LLM_BASE_URL` |

不使用 LLM 時完全不需要 API key，測驗透過逐題分數搜尋（約 5-15 次嘗試）自動通過。使用 LLM 可減少測驗嘗試次數。

</details>

<details>
<summary>所有環境變數</summary>

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

</details>

<details>
<summary>開發者資訊</summary>

### 資料目錄

所有狀態存放於 `~/.auto_tms/`：

```
~/.auto_tms/
├── session/       # Playwright 瀏覽器 session
├── state/         # 進度追蹤 (run.json, progress/, plan.json, exam_memory/)
└── logs/          # log 檔
```

### 技術架構

- **Playwright** — 無頭瀏覽器自動化
- **LLM（可選）** — CAPTCHA 辨識、測驗作答（支援 Anthropic / OpenAI / Gemini / 本地模型）
- **ddddocr** — 離線 CAPTCHA 辨識（不使用 LLM 時）
- **Pydantic** — 狀態模型與 JSON 持久化
- **Click** — CLI 框架
- **asyncio** — 並行處理多門課程

</details>

## 授權

本專案採用 [MIT License](LICENSE) 授權。

本工具僅供測試與研究用途。使用者應自行確認符合所屬機構之相關規範。
