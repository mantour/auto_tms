# auto_tms — 教育訓練系統自動化測試工具

自動完成教育訓練管理系統 (TMS) 的線上課程：影片播放、教材閱讀、問卷填寫、測驗作答。

## 功能

- **自動登入** — CAPTCHA 辨識（Claude Haiku Vision API）
- **學程規劃** — 掃描「我的學程」，計算必修/選修時數缺口，產生最少修課清單
- **課程完成** — 自動處理影片、教材、問卷、測驗（測驗使用 Claude API 作答）
- **斷點續傳** — 逐項紀錄進度，中斷後從上次狀態繼續
- **全自動流程** — plan → complete → verify，最多重試 3 輪直到所有學程通過

## 安裝

需要 Python 3.11+ 和 [uv](https://github.com/astral-sh/uv)。

```bash
git clone <repo-url> && cd auto_tms
make setup        # 建立 venv、安裝套件、下載 Chromium
make config       # 設定帳號密碼、API key、主機位址
```

## 設定

`make config` 會在專案目錄產生 `.env`（已加入 `.gitignore`）：

| 變數 | 說明 | 必填 |
|------|------|------|
| `TMS_USER` | 員工編號 | 是 |
| `TMS_PASSWD` | 密碼 | 是 |
| `ANTHROPIC_API_KEY` | Claude API key（CAPTCHA + 測驗） | 是 |
| `TMS_HOST` | TMS 主機 | 是 |
| `TMS_PROXY` | 代理伺服器，如 `socks5://127.0.0.1:1080` | 否 |

## 使用

```bash
# 查看學程完成狀況（使用快取資料）
make status

# 掃描學程、計算缺口、產生修課計畫
make plan

# 全自動執行（背景運行）
make run

# 查看即時 log
make log

# 完成單一課程
make complete ID=198761

# 停止執行中的流程
make stop
```

也可以直接使用 CLI：

```bash
auto_tms plan                        # 建立修課計畫
auto_tms complete <courseId>         # 完成單一課程
auto_tms complete-file courses.txt   # 從檔案批次完成
auto_tms run                         # 全自動流程
auto_tms -v <command>                # 顯示詳細 log
```

## 資料目錄

所有狀態存放於 `~/.auto_tms/`：

```
~/.auto_tms/
├── session/   # Playwright 瀏覽器 session
├── state/     # 進度追蹤 (progress.json, plan.json)
└── logs/      # 每日 log 檔
```

## 技術架構

- **Playwright** — 無頭瀏覽器自動化
- **Claude API** — CAPTCHA 辨識（Haiku Vision）、測驗作答（Sonnet）
- **Pydantic** — 狀態模型與 JSON 持久化
- **Click** — CLI 框架
- **asyncio** — 並行處理多門課程（上限 10）

## 作者

Yen-Ju Chu (mantour.tw@gmail.com)

## 授權

本專案採用 [MIT License](LICENSE) 授權。

本工具僅供測試與研究用途。使用者應自行確認符合所屬機構之相關規範。
