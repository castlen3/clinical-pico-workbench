# Clinical PICO Workbench

[繁體中文](README.md)

Mobile-first clinical literature assistant for turning a clinical question into a PubMed search, screening the first results, fetching abstracts, and generating a readable Traditional Chinese evidence summary.

This project is designed to run locally first: a static vanilla JS frontend, a Python standard-library HTTP server, PubMed E-utilities, and any OpenAI-compatible LLM endpoint.

## Demo

Example question: **「生物素對落髮治療有幫助嗎？」**

<p>
  <img src="docs/demo/01-query.jpeg" alt="Clinical question to PubMed query" width="260">
  <img src="docs/demo/02-progress.jpeg" alt="Progress while generating final answer" width="260">
  <img src="docs/demo/03-result.jpeg" alt="Evidence summary result" width="260">
</p>

## Who Is This For?

Clinical PICO Workbench is useful when you want a quick evidence-oriented check before spending time on a full review.

For clinicians and healthcare workers:

- Quickly turn a clinical question into a PubMed-oriented search.
- Get a first-pass screen of recent papers with PMID links.
- Use it for quick myth-busting, patient education prep, or deciding whether a claim deserves deeper review.

For general readers:

- Ask everyday health questions in plain language.
- See whether the first layer of PubMed evidence supports, weakens, or complicates a popular claim.
- Export the result as Markdown for notes or discussion with a professional.

Compared with a normal Google search, this tool tries to reduce SEO/blog noise by starting from PubMed, keeping PMID links visible, and forcing the answer to say what was actually searched and reviewed. It is best used as a fast evidence triage tool, not as a final clinical decision engine.

For anything high-stakes, controversial, or practice-changing, use this only as the first pass. Read the source papers, check guidelines, and do a deeper structured review.

## Features

- Mobile-first workflow for quick clinical PICO-style searches
- Local-first setup with LM Studio or any OpenAI-compatible endpoint
- OpenRouter-compatible configuration for people without a local model
- PubMed E-utilities search, summary, and abstract fetching
- Conservative PubMed request throttling and `efetch` batches of 4 PMID
- Lightweight AI screening before fetching abstracts
- Markdown export for note-taking
- No frontend build step and no Python package dependencies

## Requirements

1. **LLM endpoint**
   - Local: start LM Studio and enable its OpenAI-compatible server, usually `http://127.0.0.1:1234/v1`
   - Cloud: use OpenRouter or another OpenAI-compatible API

2. **Internet connection** for PubMed E-utilities.

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

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://127.0.0.1:1234/v1` | OpenAI-compatible API base URL |
| `LLM_MODEL` | `local-model` | Model name |
| `LLM_API_KEY` | empty | API key for OpenRouter/cloud endpoints |
| `APP_PORT` | `9999` | Server port |
| `PUBMED_API_KEY` | empty | Optional PubMed API key |
| `PUBMED_EMAIL` | empty | Contact email recommended by NCBI |
| `NCBI_MIN_INTERVAL_NO_KEY` | `0.38` | Minimum seconds between NCBI requests without API key |
| `NCBI_MIN_INTERVAL_WITH_KEY` | `0.12` | Minimum seconds between NCBI requests with API key |

## Mobile Workflow

```text
Step 1: Enter a clinical question -> analyze
Step 2: Confirm the PubMed query and search settings
Step 3: Search PubMed -> AI title screening -> fetch selected abstracts
Step 4: Generate per-article notes and final summary
Step 5: Export Markdown or adjust conditions and search again
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check with LLM status |
| POST | `/api/openrouter/analyze` | Question to PICO/search query |
| POST | `/api/pubmed/search` | PubMed search |
| POST | `/api/pubmed/abstracts` | Fetch abstracts |
| POST | `/api/openrouter/suggest-selection` | AI-assisted title screening |
| POST | `/api/openrouter/summarize-abstracts` | Per-article summaries |
| POST | `/api/openrouter/final-review` | Final report |
| POST | `/api/openrouter/query-optimizer` | Next-round query suggestions |
| POST | `/api/openrouter/translate-title` | Title translation |

## Architecture

- **Frontend**: dependency-free vanilla JS + CSS
- **Backend**: Python standard-library `http.server`
- **LLM**: LM Studio / OpenRouter / any OpenAI-compatible API
- **Literature**: PubMed E-utilities
- **Persistence**: browser `localStorage`

## Notes

- Local LLM users need LM Studio or another OpenAI-compatible server running before using AI features.
- PubMed E-utilities recommends no more than 3 requests/second without an API key; this project throttles requests globally and fetches abstracts in batches of 4 PMID.
- Prototype only. Not medical advice. Verify clinically important conclusions from source articles.
