# Engineering Decisions: KlypSearch

## 1. Portfolio Selection: Option B (Institutional Research Terminal)

I chose **Option B** because it presents the most complex challenge for AI synthesis. Unlike simple CRUD apps or single-agent chat interfaces, an institutional research terminal requires:
*   Reasoning across **conflicting signals** (e.g., Bearish value vs. Bullish momentum).
*   Handling both **quantitative data** (YF/News) and **unstructured text** (RAG).
*   Producing a **high-utility artifact** (a report) rather than a simple chatbot response.

## 2. Tech Stack Rationale

| Component | Technology | Rationale |
| :--- | :--- | :--- |
| **Frontend** | React / Vite | Fast development cycle and efficient local development via HMR. |
| **API Backend** | FastAPI | High performance, native async support, and excellent Pydantic integration for data validation. |
| **LLM Inference** | Groq (Llama 3.1) | Best-in-class inference speed. For a research terminal, latency (~15s) is a critical UX factor. |
| **Vector Store** | ChromaDB | Local, persistent, and serverless. Perfect for a scoped RAG implementation without infra overhead. |
| **Database** | PostgreSQL | Robust JSONB support for storing complex AI report structures alongside relational user data. |

### Alternatives Considered
*   **Next.js**: Considered for the full-stack experience, but FastAPI is significantly more robust for building complex AI orchestration logic and background tasks.
*   **Pinecone**: Decided against an external vector DB to keep the "institutional terminal" feel of having a sovereign, local knowledge base.

## 3. Multi-Tenancy Approach

I implemented a **Discriminator Column (Foreign Key) based isolation model**.
*   **Why?** In a 5-day timeline, schema-per-tenant is too complex to manage without mature migration tooling. Separate DBs per tenant would be overkill and increase deployment costs.
*   **Implementation**: Every relevant row in the database has an `organization_id`. The FastAPI dependency injection system ensures that `current_user.organization_id` is automatically propagated to every service call, creating a "software-defined boundary" that prevents cross-tenant leaks.

## 4. AI Integration & Prompt Engineering

The integration follows a **Signal-Oriented Orchestration** pattern:
*   **The Problem**: LLMs often "hallucinate" when given raw market data if they try to calculate their own percentages.
*   **The Solution**: I moved the math to Python (Market Data & Scenario Services). The AI receives pre-calculated growth rates, RSI levels, and scenario probabilities.
*   **Persona**: The system prompt enforces a "Senior Equity Analyst" persona. This forces the model to use probabilistic language ("suggests", "likelihood") rather than deterministic claims, which is critical for financial compliance and quality.

## 5. Trade-offs (5-Day Timeline)

1.  **Synchronous AI Pipeline**: In a real production environment, the 20-second report generation would be an async task (Celery/Redis). I kept it synchronous to avoid the infrastructure overhead of a task queue within the time constraint.
2.  **Hardcoded RAG Documents**: The document directory is scanned at startup. A Phase 2 improvement would be a full document upload/management UI.
3.  **Authentication**: I stuck with JWT-based Auth instead of a provider like Clerk to ensure the entire system was self-contained and could run offline/locally with ease.

## 6. If I Had 2 More Weeks...

1.  **Async Task Architecture**: Move report generation to a background worker to allow users to navigate away while the report "builds."
2.  **Expanded Data Sources**: Integrate FRED (Macro data) and EDGAR (Raw SEC filings) for a more comprehensive data block.
3.  **Portfolio Analysis**: Add a second agent that reviews the user's *entire* watchlist and highlights correlation risks across multiple tickers.
4.  **Multi-Agent Collaborative Flow**: Implement a true multi-agent system where a "Technicals Analyst" and "Value Analyst" argue their cases before a "Lead Analyst" makes the final recommendation.

## 7. The Hardest Part & Solution

The hardest part was **Conflict Resolution in the Prompt**. LLMs tend to be agreeable and will often ignore a bearish MACD if the overall consensus is positive.
*   **Solution**: I added explicit **"Reasoning Rules"** to the system prompt that force the model to acknowledge overbought RSI levels or high volatility specifically. I also used a "Constraint Injection" technique where I pass the count of Buy/Sell ratings separately to force the model to see the full distribution of professional opinions.
