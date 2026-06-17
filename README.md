# KlypSearch

**AI-powered institutional research terminal**

KlypSearch combines market data, news intelligence, sentiment analysis, quantitative risk modeling, technical indicators, scenario forecasting, and document retrieval into a single research workflow. It turns raw financial data into institutional-style research reports and conversational investment insights.

Built for investors, analysts, students, and anyone who wants a faster way to research a stock.

## Features

**Institutional research reports**
One-click stock analysis with an AI-generated investment thesis, a conviction score, and a clear recommendation.

**Market data engine**
Real-time prices, market cap, revenue growth, EPS, P/E and forward P/E, and profitability metrics.

**News intelligence**
Aggregated company news with bullish, neutral, or bearish sentiment classification.

**Risk engine**
Annualized volatility, maximum drawdown, Sharpe ratio, beta versus the S&P 500, and an overall risk classification.

**Technical analysis**
RSI (14), SMA 50 and SMA 200, golden cross detection, and trend/momentum signals.

**Scenario modeling**
Bull, base, and bear cases with a probability-weighted outlook.

**Research assistant chat**
Natural language queries that support multi-company comparisons and AI-synthesized answers.

**Document knowledge base**
Earnings reports and company filings indexed for Retrieval-Augmented Generation (RAG) via ChromaDB.

**Authentication and workspaces**
JWT authentication with multi-user support and secured API access.

## Example queries

Research reports take a ticker directly:

```text
NVDA
AAPL
MSFT
TSLA
TCS.NS
BHEL.NS
```

The research assistant takes natural language:

```text
NVDA vs AMD

Compare Microsoft and Amazon growth

Analyze NVIDIA earnings and summarize risks

Compare JPMorgan, Goldman Sachs and Morgan Stanley capital positions

What are Tesla's biggest risks right now?
```

## Tech stack

**Backend**
[FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy](https://www.sqlalchemy.org/), [Alembic](https://alembic.sqlalchemy.org/), [Groq](https://groq.com/), [yfinance](https://github.com/ranaroussi/yfinance), [ChromaDB](https://www.trychroma.com/), JWT authentication.

**Frontend**
[React](https://react.dev/), [Vite](https://vitejs.dev/), JavaScript, terminal-inspired UI.

**AI and data**
Groq LLM inference, Yahoo Finance market data, financial news APIs, ChromaDB vector search, Retrieval-Augmented Generation (RAG).

## Interesting techniques

- **Layered architecture with strict separation of concerns** — routes only handle HTTP (validate input, call a service, return a response), while services own all business logic and external calls. This keeps controllers thin and easy to test.
- **JWT-based auth via dependency injection** — [`middleware/dependencies.py`](backend/app/middleware/dependencies.py) uses FastAPI's [`Depends`](https://fastapi.tiangolo.com/tutorial/dependencies/) system to inject the authenticated user into routes instead of repeating auth checks in every endpoint.
- **Retrieval-Augmented Generation (RAG)** — [`document_service.py`](backend/app/services/document_service.py) indexes earnings reports and filings in ChromaDB, retrieves the most relevant chunks for a query, and feeds that context to the LLM before it generates a report or chat response.
- **Versioned database migrations with Alembic** — schema changes live in [`alembic/versions/`](backend/alembic/versions/) instead of being applied by hand, so the database schema has a reviewable history.
- **Pydantic schema validation** — request and response contracts are defined separately from the SQLAlchemy models, so the API's public shape can evolve independently of how data is stored.
- **Quantitative risk modeling** — the risk engine computes annualized volatility, maximum drawdown, Sharpe ratio, and beta, standard tools for comparing risk-adjusted returns across assets.
- **Technical indicator pipeline** — RSI and moving averages (SMA 50/200) are computed server-side, including golden cross detection, a classic momentum signal where a shorter moving average crosses above a longer one.
- **Vite dev server with HMR** — the frontend dev workflow uses Vite's [Hot Module Replacement](https://vite.dev/guide/features.html#hot-module-replacement) for near-instant feedback while editing UI.

## Quick setup

### Backend

```bash
cd backend

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp env.example .env
```

Add your environment variables to `.env`:

```env
GROQ_API_KEY=your_key_here
SECRET_KEY=your_secret_key
```

Run the backend:

```bash
python -m uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`.

### Frontend

```bash
cd frontend

npm install
npm run dev
```

The app runs at `http://localhost:5173`.

## Project structure

```
klypsearch/
├── backend/
│   ├── alembic/
│   │   └── versions/
│   ├── app/
│   │   ├── core/
│   │   ├── database/
│   │   ├── middleware/
│   │   ├── models/
│   │   ├── routes/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── utils/
│   └── data/
│       ├── documents/
│       └── chroma_db/
└── frontend/
    ├── public/
    └── src/
        ├── components/
        └── assets/
```

**[`backend/app/services/`](backend/app/services/)** — where the actual work happens: market data, news, sentiment, risk, technical analysis, scenario modeling, document retrieval, and AI synthesis each get their own service.

**[`backend/data/documents/`](backend/data/documents/)** — earnings reports and filings that get ingested into ChromaDB for the RAG pipeline.

**`backend/data/chroma_db/`** — local vector store; gitignored and rebuilt from the documents folder.

**[`frontend/src/components/`](frontend/src/components/)** — React components for the terminal UI.

**`frontend/src/assets/`** — static assets used by the frontend (icons, images).


## Conventions

| Layer | Responsibility |
|---|---|
| `routes/` | HTTP only — validate input, call service, return response |
| `services/` | Business logic, external API calls, orchestration |
| `schemas/` | Pydantic models for API contracts |
| `models/` | SQLAlchemy DB tables |
| `components/` | React UI (terminal theme) |

## Future improvements

- Portfolio tracking
- Earnings calendar
- Advanced valuation models
- Multi-LLM support
- PDF report export
- Real-time market alerts

## Disclaimer

KlypSearch is intended for educational and research purposes only. It does not constitute financial advice, investment recommendations, or guarantees regarding future market performance. Always conduct your own research before making investment decisions.

---

Built with FastAPI, React, Groq, Yahoo Finance, and ChromaDB.
