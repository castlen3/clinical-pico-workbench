# Clinical PICO Workbench

Mobile-first clinical literature assistant for turning a clinical question into a PubMed search, screening the first results, fetching abstracts, and generating a readable Traditional Chinese evidence summary.

This project is designed to run locally first: a static vanilla JS frontend, a Python standard-library HTTP server, PubMed E-utilities, and any OpenAI-compatible LLM endpoint.

## Features

- Mobile-first workflow for quick clinical PICO-style searches
- Local-first setup with LM Studio or any OpenAI-compatible endpoint
- OpenRouter-compatible configuration for people without a local model
- PubMed E-utilities search, summary, and abstract fetching
- Conservative PubMed request throttling and `efetch` batches of 4 PMID
- Lightweight AI screening before fetching abstracts
- Markdown export for note-taking
- No frontend build step and no Python package dependencies

## 前置需求

1. **LLM endpoint**
   - Local: start LM Studio and enable its OpenAI-compatible server, usually `http://127.0.0.1:1234/v1`
   - Cloud: use OpenRouter or another OpenAI-compatible API

2. **網路連線**：PubMed API 需要外網連線

## Quick Start

```bash
python3 server.py --host 0.0.0.0 --port 9999
```

Open on the same computer:

```text
http://127.0.0.1:9999
```

Open from a phone on the same Wi-Fi:

```text
http://<your-computer-lan-ip>:9999
```

On macOS, you can also double-click:

- `start_clinical_pico.command`
- `stop_clinical_pico.command`

The start script opens the browser and closes its launcher Terminal window after startup.

## LLM Configuration

Copy the example environment file if you want a local config:

```bash
cp .env.example .env
```

Then export the variables before starting, or prefix the command.

### LM Studio

```bash
LLM_BASE_URL=http://127.0.0.1:1234/v1 \
LLM_MODEL=your-local-model-name \
python3 server.py --host 0.0.0.0
```

### OpenRouter

```bash
LLM_BASE_URL=https://openrouter.ai/api/v1 \
LLM_MODEL=google/gemini-2.5-flash-lite \
LLM_API_KEY=sk-or-v1-... \
python3 server.py --host 0.0.0.0
```

## 環境變數

| 變數 | 預設值 | 說明 |
|---|---|---|
| `LLM_BASE_URL` | `http://127.0.0.1:1234/v1` | OpenAI-compatible API base URL |
| `LLM_MODEL` | `local-model` | 模型名稱 |
| `LLM_API_KEY` | (空) | API key for OpenRouter/cloud endpoints |
| `APP_PORT` | `9999` | 服務 port |
| `PUBMED_API_KEY` | (空) | PubMed API key（選填，提高速率） |
| `PUBMED_EMAIL` | (空) | NCBI 建議提供的聯絡信箱 |
| `NCBI_MIN_INTERVAL_NO_KEY` | `0.38` | 無 PubMed API key 時，每次 NCBI request 至少間隔秒數 |
| `NCBI_MIN_INTERVAL_WITH_KEY` | `0.12` | 有 PubMed API key 時，每次 NCBI request 至少間隔秒數 |

## 手機操作流程

```
Step 1: 輸入臨床問題 → 分析問題
Step 2: 確認 PICO + 查詢式 → 搜尋 PubMed
Step 3: 瀏覽文獻 → AI 輔助勾選 → 載入摘要
Step 4: 查看逐篇中文重點 → 產生最終報告
Step 5: 最終結論 + 下一輪查詢建議
```

## API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/health` | 健康檢查（含 LM Studio 連線狀態） |
| POST | `/api/openrouter/analyze` | PICO 分析 |
| POST | `/api/pubmed/search` | PubMed 搜尋 |
| POST | `/api/pubmed/abstracts` | 摘要抓取 |
| POST | `/api/openrouter/suggest-selection` | AI 輔助勾選 |
| POST | `/api/openrouter/summarize-abstracts` | 逐篇摘要 |
| POST | `/api/openrouter/final-review` | 最終報告 |
| POST | `/api/openrouter/query-optimizer` | 查詢建議 |
| POST | `/api/openrouter/translate-title` | 標題翻譯 |

## 技術架構

- **前端**：零依賴 vanilla JS + CSS，單一 app.js（~950 行）
- **後端**：Python 標準庫 http.server（ThreadingHTTPServer）
- **LLM**：LM Studio OpenAI-compatible API（可切換至 OpenRouter、Ollama 等）
- **文獻**：PubMed E-utilities（esearch / esummary / efetch）
- **持久化**：localStorage（前端 state）

## 控制中心

- `control_center.command`：單一入口選單
- `status_services.command`：掃描服務狀態
- `recover_services.command`：自動修復服務

## 注意事項

- Local LLM users need LM Studio or another OpenAI-compatible server running before using AI features
- PubMed E-utilities 無 API key 時建議不超過 3 req/sec；本專案會做全域節流，且摘要 efetch 每批最多 4 篇
- 手機 Safari 請注意：地址列收合時 100dvh 會有跳動，已用 `svh` 降解處理
- Prototype only. Not medical advice. Verify clinically important conclusions from source articles.
