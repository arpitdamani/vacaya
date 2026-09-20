---
title: Vacaya
emoji: 🧳
sdk: docker
app_port: 7860
---

# Vacaya - multi-agent travel planner

Enter origin, destination, dates, budget (USD/INR) and preferences. Three specialist agents search Amadeus in parallel
(flights, hotels, activities), a deterministic orchestrator checks the total against the budget and re-runs whichever
agent overshot with a tighter cap, then a writer agent produces a day-by-day itinerary. Plans are saved to SQLite.

```
React UI (21st.dev components) --EventSource--> FastAPI /api/plan (SSE) --> orchestrator.plan()
                                                                              |-- resolve cities (Amadeus Locations)
                                                                              |-- split budget 40/35/25
                                                                              |-- asyncio.gather: flight | stay | activity agents (OpenAI Agents SDK + Amadeus tools)
                                                                              |-- validate total; retry worst offender with reduced cap (max 2)
                                                                              '-- writer agent -> Markdown itinerary -> SQLite
```

| File | Role |
|---|---|
| `specialists.py` | Amadeus `function_tool`s, Pydantic output models, the three specialist agents and the writer agent |
| `orchestrator.py` | Budget split, parallel run, validation/retry loop, itinerary prompt |
| `api.py` | FastAPI: SSE progress stream, plan history routes, serves `web/dist` |
| `db.py` | SQLite plan history |
| `web/` | Vite + React + Tailwind + shadcn; Booking Form and AI Task List from 21st.dev |

## Run locally

1. `python -m venv .venv && .venv/Scripts/activate` (Windows) or `source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `OPENAI_API_KEY` from https://platform.openai.com
   - `AMADEUS_CLIENT_ID` / `AMADEUS_CLIENT_SECRET` from https://developers.amadeus.com (Self-Service, free test environment)
4. Backend: `uvicorn api:app --reload --port 8000`
5. Frontend: `cd web && npm install && npm run dev`, then open http://localhost:5173

Or build once and serve everything from FastAPI: `cd web && npm run build`, then open http://localhost:8000.

## Tests

`python -m pytest` - no network; agents, Amadeus and the orchestrator are faked.

## Deploy (Hugging Face Spaces, Docker)

1. Create a Space at https://huggingface.co/new-space with SDK **Docker**.
2. Space settings -> Variables and secrets: add `OPENAI_API_KEY`, `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET` as secrets.
3. `git remote add hf https://huggingface.co/spaces/<user>/vacaya && git push hf master:main`

Plan history lives in a SQLite file inside the container, which resets on rebuild.

## Amadeus test environment caveats

The free test environment only has data for major cities and hotel offers are often empty. Known-good demo inputs:
Madrid -> Paris, London -> Barcelona, New York -> San Francisco. If a section reports "no options found", that is the
test data, not a bug - the agents never invent prices.
