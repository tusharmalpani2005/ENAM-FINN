# FINN

**Intelligent Investment Research & Filing Intelligence for Indian Equities**

FINN is a lightweight, high-signal investment intelligence platform built for Indian public equity markets (NSE/BSE). It unifies corporate filings, earnings disclosures, financial fundamentals, market price movements, and an investor's professional network into a responsive decision-support system.

---

## Key Features

### 1. Push: Real-Time Filings & Anomaly Alerts
- **Automated Filing Classification**: Categorizes regulatory announcements (Quarterly Results, Management Changes, Litigation, M&A, Capex, Fund Raising, Credit Ratings, Insider Trading, etc.) with priority scoring.
- **Results Parser**: Automatically parses financial disclosure tables, computing QoQ and YoY metrics for Revenue, EBITDA, PBT, and PAT (in ₹ crore) alongside margin expansion/contraction in basis points.
- **Market Anomaly Alerts**: Monitors trading volume surges and sharp price moves against configurable thresholds (`vol_x`, `px`).
- **Customizable Digests**: Filterable feeds across rolling lookback windows (1 to 720 hours) and specific filing categories.

### 2. Pull: Grounded Fundamental Research
- **Zero Hallucination Guarantee**: Fundamental insights are grounded strictly in primary disclosures (Annual Report tables, notes to accounts, and earnings call transcripts). Every quantitative claim includes a provenance tag.
- **Guidance vs. Delivery Tracker**: Extracts management forward guidance (revenue targets, EBITDA margins, capex plans) from concalls and tracks subsequent execution across quarters.
- **Financial Quality Indicators**: Flags audit qualifications, contingent liabilities, and accounting shifts.

### 3. The Network: Executive Diligence & Warm Introductions
- **Network Graph**: Maps relationships between portfolio companies, executives, board members, suppliers, competitors, and key counterparties.
- **Executive Diligence Dossiers**: Comprehensive career trajectories, past organizational tenures, educational backgrounds, and personal relationship notes.
- **"Who to Call" Routing**: Recommends high-affinity contacts, industry veterans, and former colleagues in your network to diligence specific events or companies.
- **Theme Expertise**: Surfaces domain experts across investment themes (e.g., Specialty Chemicals, Auto Tech, Banking & Credit, EPC).

### 4. Ask: Conversational Financial Copilot
- **Context-Aware Reasoning**: Free-text query engine (`/api/ask`) that synthesizes portfolio allocations, recent price action, latest quarterly results, and network insights to answer investor questions.
- **Flexible LLM Integration**: Single-seam LLM provider supporting **Hyde** and **Groq** (OpenAI-compatible) with fast deterministic fallbacks if API keys are missing or limits are reached.
- **Model Telemetry**: Live endpoint (`/api/llm`) displaying request counts, latency status, and error metrics.

### 5. Mobile-First Responsive Interface
- **Modern Minimalist UI**: Fast, responsive layout with single-page navigation across **Today**, **Ask**, and **Network** tabs.
- **Visual Analytics**: Native SVG sparklines and quarterly bar charts for rapid financial trend visual inspection.
- **Light & Dark Theme**: Built-in theme toggle with persisted user preference.

---

## Architecture & Project Structure

```
.
├── app/
│   ├── main.py        # FastAPI entrypoint, routing, and static file mount
│   ├── push.py        # Exchange filing classification, results parsing, alert logic
│   ├── pull.py        # Grounded fundamental research & guidance extraction
│   ├── network.py     # Network graph, executive dossiers, warm routing
│   ├── router.py      # Context aggregation and natural language Q&A engine
│   ├── llm.py         # LLM abstraction seam (Groq / Hyde) with graceful fallback
│   ├── store.py       # Data abstraction layer (live overrides + fallback data)
│   └── seed.py        # Fallback dataset (companies, relationships, people, baseline data)
├── data/
│   └── live/          # Live snapshot caches (prices, announcements, fundamentals)
├── static/
│   ├── index.html     # Single-page web application shell
│   ├── style.css      # Design tokens, theme variables, layout styling
│   └── app.js         # Reactive UI renderer, charts, sheet interactions
├── Dockerfile         # Containerized production deployment
├── requirements.txt   # Python dependencies
└── .env.example       # Sample environment configuration
```

---

## Getting Started

### Prerequisites
- Python 3.10+ (Python 3.12 recommended)
- `pip` or `uv`

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/tusharmalpani2005/ENAM-FINN.git
   cd ENAM-FINN
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment Variables (Optional):**
   Copy `.env.example` to `.env` if you wish to enable the LLM reasoning layer:
   ```bash
   cp .env.example .env
   ```
   Configure either **Hyde** or **Groq**:
   ```env
   LLM_PROVIDER=hyde
   HYDE_INFERENCE_API_KEY=your_hyde_api_key_here
   HYDE_MODEL=hyde/glm-5.3-flash
   ```
   *(If left unconfigured, FINN operates completely in deterministic mode without failing).*

---

## Running the Application

### Local Development
Start the FastAPI application using Uvicorn:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Open your browser and navigate to:
```
http://localhost:8000
```

### Running with Docker
Build and launch the containerized application:
```bash
docker build -t finn .
docker run -p 8000:8000 finn
```

---

## API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/api/meta` | `GET` | Returns portfolio companies, active themes, categories, and live layer status. |
| `/api/digest` | `GET` | Returns classified announcements within a given lookback window (`hours`, `cats`). |
| `/api/alerts` | `GET` | Computes price and volume anomaly alerts (`vol_x`, `px`). |
| `/api/research/{ticker}` | `GET` | Returns grounded fundamental research, quarters, and guidance history. |
| `/api/network/graph/{ticker}` | `GET` | Returns organizational relationships, competitors, suppliers, and buyers. |
| `/api/network/person/{pid}` | `GET` | Returns background diligence dossier for an executive or director. |
| `/api/network/expertise` | `GET` | Finds key network contacts with domain expertise for a theme (`theme`). |
| `/api/network/warm` | `GET` | Generates keep-warm outreach prompts based on recent announcements. |
| `/api/ask` | `POST` | Processes conversational investor questions with context (`{"q": "..."}`). |
| `/api/llm` | `GET` | Returns live LLM stats, active provider, model name, and error counts. |

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
