# Multi-Agent Travel Planner — Design

**Date:** 2026-09-21
**Purpose:** Portfolio demo. Real data via Amadeus, multi-agent orchestration via OpenAI Agents SDK, React UI built from 21st.dev components served by FastAPI, hosted as one Docker container on Hugging Face Spaces.

## Decisions

| Question | Decision |
|---|---|
| Data source | Amadeus Self-Service (test env): Flight Offers Search, Hotel List + Hotel Offers Search, Tours & Activities, Locations |
| Language / LLM | Python, OpenAI Agents SDK (`openai-agents`) |
| Interface | React (Vite + TypeScript + Tailwind + shadcn) using 21st.dev components (Booking Form, AI Task List), served by FastAPI. Progress streamed over SSE. |
| Persistence | SQLite plan history (ephemeral on Hugging Face Spaces — resets on rebuild; acceptable for demo) |
| Budget overage | Retry loop: re-run the worst-offending agent with a tighter cap, max 2 retries |
| Currency | User selects USD or INR. Flights/hotels requested in that currency via Amadeus `currencyCode`; activity prices converted with one rate fetch from frankfurter.app |
| Hosting | Hugging Face Spaces, Docker SDK, port 7860 (free; secrets in Space settings) |

## Inputs

origin city, destination city, depart date, return date, travelers (int), budget (number), currency (USD/INR), preferences (free text).

## Architecture

```
web/ (React, 21st.dev components) ── EventSource GET /api/plan?… ──► api.py (FastAPI, SSE)
                                                                       │  calls plan(request, on_event); on_event → SSE events
                                                                       ▼
orchestrator.py                        specialists.py
   resolve cities ──── Amadeus Locations (code, no LLM)
   split budget 40/35/25
   asyncio.gather ───► flight_agent   ── search_flights    (Flight Offers Search)
                  ───► stay_agent     ── search_hotels     (Hotel List → Hotel Offers)
                  ───► activity_agent ── search_activities (Tours & Activities by geo)
   validate total; retry worst offender ≤2×
   writer_agent ◄── all three results ── markdown itinerary
   │
   ▼
db.py  save(request, itinerary, totals) / list() / load(id)
```

The orchestrator is deterministic Python, not an LLM. Specialist agents are LLM agents with tools and typed outputs. The writer agent is an LLM agent with no tools.

## Files

- `api.py` — FastAPI. `GET /api/plan?origin&destination&depart&return_date&travelers&budget&currency&preferences` streams `text/event-stream`: one `{kind, status, detail}` event per `on_event` call, then a final `{kind:"plan", status:"done", itinerary, totals, over_budget}` (or `status:"error"`). `GET /api/plans` lists saved plans, `GET /api/plans/{id}` returns one. Mounts `web/dist` as static files when it exists.
- `web/` — Vite + React + TypeScript + Tailwind + shadcn. 21st.dev components fetched through the 21st.dev MCP connector: Booking Form (adapted to our inputs) and AI Task List (one task per agent; retries shown as subtasks). Itinerary rendered with `react-markdown`. Sidebar of past plans from `/api/plans`.
- `Dockerfile` — two-stage: node builds `web/dist`, python image runs `uvicorn api:app` on 7860.
- `orchestrator.py` — `async def plan(req: PlanRequest, on_event) -> PlanResult`. Budget split constants, gather, validate/retry, writer call, FX conversion.
- `specialists.py` — Amadeus client, `@function_tool`s, Pydantic output models, four `Agent` definitions. (Not `agents.py` — that name collides with the SDK package.)
- `db.py` — SQLite, one table `plans(id, created_at, request_json, itinerary_md, total, currency)`.
- `test_orchestrator.py` — budget retry logic with fake runners; no network.
- `requirements.txt`, `.env.example`, `README.md`.

## Specialist agents

Each gets: destination code/name, dates, travelers, its budget cap, currency, and the preferences text. Each has `output_type` set to a Pydantic model so the orchestrator can read `total` without parsing prose. Each is instructed to pick the option that best fits the preferences within the cap, explain the pick in `reason`, and return an empty result with `reason="no options found"` rather than invent data.

- **Flight agent** — tool `search_flights(origin, dest, depart, return, adults, max_price, currency)` → up to 5 offers (airline, times, stops, price). Output `FlightPick{airline, depart_at, return_at, stops, price, reason}`.
- **Stay agent** — tool `search_hotels(city_code, check_in, check_out, adults, currency)` → hotel IDs by city, then offers for the first N (Amadeus limits IDs per call). Output `StayPick{name, room, nightly, total, reason}`.
- **Activity agent** — tool `search_activities(lat, lon, radius_km)` → activities with name, short description, price+currency. Output `ActivityPlan{items: [{name, price, day_hint}], total, currency, reason}`. Orchestrator converts `total` to the plan currency.
- **Writer agent** — no tools. Input: request + three results + over-budget flag. Output: markdown with a day-by-day plan (one heading per date), a cost table (flights / stay / activities / total vs budget), and a one-line "why these picks" per section drawn from the `reason` fields.

Model: `OPENAI_MODEL` env var, default `gpt-4.1-mini`; same model for all agents.

## Budget loop

```
caps = {flight: 0.40, stay: 0.35, activity: 0.25} × budget
results = gather(run each agent with its cap)
for attempt in range(2):
    total = sum(r.total)
    if total <= budget: break
    worst = agent with max(r.total - caps[agent])
    caps[worst] -= (total - budget)
    results[worst] = run(worst, caps[worst])
over_budget = total > budget after loop
```

`on_event(agent_name, status, detail)` fires on start, done (with cost), and retry (with new cap) so the UI can show the validation step.

## Error handling

- Amadeus errors → tool returns `{"error": ...}`, empty results → `[]`; agent reports "no options found"; itinerary marks that section unavailable. Prices are never fabricated.
- FX fetch failure → activity total left in source currency and flagged in the itinerary rather than guessed.
- Missing API keys → `/api/plan` returns HTTP 503 with the missing names; the UI shows that message.
- Input validation at the API boundary: return after depart, travelers 1–9, budget > 0, currency ∈ {USD, INR}; violations are HTTP 422.
- Amadeus test env only covers major cities and hotel offers are often empty; README lists known-good demo inputs (e.g. MAD→PAR, NYC→LON).

## Testing

`test_api.py` monkeypatches `plan` with a fake that emits two events and returns a fixed result, then asserts the SSE body contains those events in order followed by the final `plan/done` event, and that the plan was saved.

`test_orchestrator.py` monkeypatches the per-agent runner with fakes returning fixed totals and asserts:
1. total under budget → each agent runs once;
2. total over budget → worst offender re-runs with cap reduced by the overage;
3. still over after 2 retries → loop stops, `over_budget=True`.

No live OpenAI or Amadeus calls in tests.

## Out of scope

Token-level streaming of agent reasoning, configurable budget split, currencies beyond USD/INR, hosted DB, booking/checkout, multi-city trips, user accounts, dark/light theme toggle beyond what the 21st.dev components ship with.
