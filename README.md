# EDIA — Enterprise Data Intelligence Assistant

EDIA is an AI-powered enterprise intelligence platform that combines RAG retrieval,
LLM reasoning, ERP analytics, and market intelligence into one conversational system.
Ask questions in natural language about your business data — EDIA plans, executes,
and streams evidence-backed answers with charts and reports.

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────┐     ┌──────────────┐
│   Frontend  │────▶│                Backend (FastAPI)          │────▶│  Datasources │
│  React/Vite │     │  Hermes agent runtime (planner + skills)  │     │  MySQL / CSV  │
└─────────────┘     │  RAG (Chroma + bge-m3 embeddings)         │     │  ERP / Market │
                    │  LLM layer (local Ollama + external LLM)  │     └──────────────┘
                    │  Intelligence engine (news + events)      │
                    │  Forecasting (ARIMA, seasonal)            │
                    └──────────────────────────────────────────┘
```

## Tech Stack

- **Backend:** Python 3, FastAPI, SQLAlchemy, pymysql, ChromaDB, sentence-transformers, pandas
- **Frontend:** React 18 + TypeScript + Vite, Tailwind, Leaflet, jsPDF, html2canvas
- **LLM:** Local Ollama (e.g. `qwen3.5-27b`) + external DeepSeek (`deepseek-chat`)
- **RAG:** Hybrid retrieval over industry reports with `BAAI/bge-m3` embeddings
- **Deploy:** Vercel (`vercel.json`), scripts for local/cloud run

## Key Capabilities

- Natural-language querying over ERP, sales, and market data with a planning engine
- Streaming chat UI with live plan checklist and tool-evidence completion
- RAG over industry reports (weekly briefings, market metrics, causal narratives)
- Intelligence engine: news ingestion, event extraction, scenario probability, second-order effects
- Forecasting and seasonal analytics (ARIMA refits, daily calibration)
- Weekly report generation with org filters and chart rendering
- Role-based auth with forced password change on first login

## Quick Start

```bash
# 1. Configure environment (copy from example)
cp .env.example .env
#    — set EXTERNAL_LLM_API_KEY, DB connection, RAG options

# 2. Backend (cloud profile, or use scripts/run-backend-local.sh)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
RESTART_BACKEND=1 ./scripts/run-backend-cloud.sh

# 3. Frontend
cd frontend && npm install && npm run dev
```

Smoke tests after boot:

- Chat UI stream: *"make market weekly report"* → full weekly markdown report
- ERP query: *"this month erp report"* or *"this month sales revenue"*

## Project Layout

```
api/            Vercel serverless entry (wraps backend.server)
backend/
  src/
    hermes/         Agent runtime: intent planner, skills, execution, streaming
    nlu/            Intent parsing, context validation, LLM service
    planner/        Query planning, schema registry, prerenderer
    rag/            Hybrid retrieval, report chunker/ingester, vector store
    intelligence/   News ingestion, events, narratives, scenarios
    analytics/      Seasonal analytics
    db/             Engine factory, MySQL/CSV/SQLite ingest, ERP, market data
    domain/         Ontology, knowledge graph, models, product resolver
    service/        Auth, alerts, market data, pricing, report generation
  scripts/          Chroma index rebuild, ARIMA refit
frontend/       React + TypeScript UI (Vite)
scripts/        Run/build helpers
```

## License

Proprietary — all rights reserved.
