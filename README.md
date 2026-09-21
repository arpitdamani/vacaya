# Vacaya - multi-agent travel planner

Enter origin, destination, dates, budget (USD/INR) and preferences. Three specialist agents search Google Flights, Google Hotels and Google's top-sights results (via SerpApi) in parallel
(flights, hotels, activities), a deterministic orchestrator checks the total against the budget and re-runs whichever
agent overshot with a tighter cap, then a writer agent produces a day-by-day itinerary. Plans are saved to SQLite.

```
React UI (21st.dev components) --EventSource--> FastAPI /api/plan (SSE) --> orchestrator.plan()
                                                                              |-- split budget 40/35/25
                                                                              |-- asyncio.gather: flight | stay | activity agents (OpenAI Agents SDK + SerpApi tools)
                                                                              |-- validate total; retry worst offender with reduced cap (max 2)
                                                                              '-- writer agent -> Markdown itinerary -> SQLite
```

| File | Role |
|---|---|
| `specialists.py` | SerpApi `function_tool`s (Google Flights / Hotels / top sights), Pydantic output models, the three specialist agents and the writer agent |
| `orchestrator.py` | Budget split, parallel run, validation/retry loop, itinerary prompt |
| `api.py` | FastAPI: SSE progress stream, plan history routes, serves `web/dist` |
| `db.py` | SQLite plan history |
| `web/` | Vite + React + Tailwind + shadcn; Booking Form and AI Task List from 21st.dev |

## Run locally

1. `python -m venv .venv && .venv/Scripts/activate` (Windows) or `source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `OPENAI_API_KEY` from https://platform.openai.com
   - `SERPAPI_API_KEY` from https://serpapi.com (free plan: 250 searches/month; a plan uses 3, plus 1 per budget retry)
4. Backend: `uvicorn api:app --reload --port 8000`
5. Frontend: `cd web && npm install && npm run dev`, then open http://localhost:5173

Or build once and serve everything from FastAPI: `cd web && npm run build`, then open http://localhost:8000.

## Tests

`python -m pytest` - no network; agents, SerpApi and the orchestrator are faked.

## Deploy (Render, free)

`render.yaml` describes the service, so:

1. https://dashboard.render.com -> **New -> Blueprint** -> connect this GitHub repo -> **Apply**.
2. When prompted, paste `OPENAI_API_KEY` and `SERPAPI_API_KEY`.

Render builds the Dockerfile, binds `$PORT`, and redeploys on every push to `master`. Free instances sleep after
15 minutes idle and take ~50 s to wake. Plan history lives in a SQLite file inside the container, which resets on
every deploy.

## Data caveats

Flight prices are Google Flights' round-trip totals for the outbound options it lists (Google pairs the cheapest return);
hotel prices are Google Hotels' lowest listed rate for the stay; activity prices come from Google's "top sights" cards and
are converted with a live exchange rate. Nothing is bookable from here, and the agents never invent a price - a section
with no results says so.
