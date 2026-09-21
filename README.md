# Vacaya - multi-agent travel planner

Enter origin, destination, dates, budget (USD/INR) and preferences. Three specialist agents search Google Flights, Google Hotels, Google's top sights and Google Maps (via SerpApi) in parallel
(flights, hotel, experiences + dining + nightlife), a deterministic orchestrator checks the total against the budget and re-runs whichever
agent overshot with a tighter cap, then a writer agent produces a day-by-day itinerary. Plans are saved to SQLite.

```
React UI (21st.dev components) --EventSource--> FastAPI /api/plan (SSE) --> orchestrator.plan()
                                                                              |-- split budget 35/30/35
                                                                              |-- asyncio.gather: flight | stay | activity agents (OpenAI Agents SDK + SerpApi tools)
                                                                              |-- validate total; retry worst offender with reduced cap (max 2)
                                                                              '-- writer agent -> structured itinerary (days / slots / item refs) -> SQLite
```

| File | Role |
|---|---|
| `specialists.py` | SerpApi `function_tool`s (Google Flights / Hotels / top sights / Maps), Pydantic output models, the three specialist agents and the writer agent |
| `orchestrator.py` | Budget split, parallel run, validation/retry loop, itinerary prompt |
| `api.py` | FastAPI: SSE progress stream, plan history routes, serves `web/dist` |
| `db.py` | SQLite plan history |
| `web/` | Vite + React + Tailwind + shadcn; Booking Form and AI Task List from 21st.dev; `Itinerary.tsx` renders day → slot → note bullets + place cards (photo, kind · ★rating · price, links to Google Maps) |

## Run locally

1. `python -m venv .venv && .venv/Scripts/activate` (Windows) or `source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `OPENAI_API_KEY` from https://platform.openai.com
   - `SERPAPI_API_KEY` from https://serpapi.com (free plan: 250 searches/month; a plan uses 5-7, plus 1-5 per budget retry)
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

- Flight prices are Google Flights' round-trip totals for the outbound options it lists (Google pairs the cheapest return).
- Hotel prices are Google Hotels' lowest listed rate for the stay.
- Sights use Google's listed ticket price; restaurants, water sports, tours and nightlife come from Google Maps, which has
  no prices, so the experience agent gives a typical price and the itinerary marks it `~... est.` with a note under the
  cost table. Listed prices are never invented; estimates are never presented as quotes.
- Photos are Google/SerpApi CDN thumbnails; the page sends no referrer so they load, and any that 404 are hidden.
- The experience agent works "knowledge proposes, search verifies": it decides what a local would recommend, then uses
  targeted Google Maps queries to confirm each place (rating, reviews, photo, hours, link). Only verified places are used;
  picks need ★4.3+/300+ reviews unless nothing else fits. Days are clustered by neighbourhood and slots respect opening hours.
- The writer agent assigns items to days/slots and must respect flight times; `orchestrator.reconcile()` then guarantees every
  item appears exactly once (dropped items go to the lightest day) so the UI never loses a pick. A budget retry that returns nothing is rejected in favour of the original pick.
- Nothing is bookable from here.
- City suggestions (`web/src/lib/cities.json`) are every city with a scheduled-service large/medium airport in the
  public-domain [OurAirports](https://ourairports.com/data/) dataset, plus a few tourist places without their own airport (Goa, Bali, Kyoto).
  Free text is still accepted.
