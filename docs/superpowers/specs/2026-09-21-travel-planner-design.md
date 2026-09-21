# Multi-Agent Travel Planner — Design

**Date:** 2026-09-21
**Purpose:** Portfolio demo. Real data via SerpApi (Google Flights / Hotels / top sights), multi-agent orchestration via OpenAI Agents SDK, React UI built from 21st.dev components served by FastAPI, hosted as one Docker container on Render's free tier.

## Decisions

| Question | Decision |
|---|---|
| Data source | SerpApi (free 250 searches/month): `google_flights`, `google_hotels`, `google` engine `top_sights`, `google_maps` for experiences/dining/nightlife. Amadeus Self-Service was decommissioned 2026-07-17. ~5–7 searches per plan. |
| Language / LLM | Python, OpenAI Agents SDK (`openai-agents`) |
| Interface | React (Vite + TypeScript + Tailwind + shadcn) using 21st.dev components (Booking Form, AI Task List), served by FastAPI. Progress streamed over SSE. |
| Persistence | SQLite plan history (ephemeral on Render — resets on each deploy; acceptable for demo) |
| Budget overage | Retry loop: re-run the worst-offending agent with a tighter cap, max 2 retries; stops early when a retry returns the same total (market floor); a retry that returns nothing is rejected and the original pick kept |
| Currency | User selects USD or INR. Flights/hotels requested in that currency via SerpApi `currency`; activity prices parsed from Google's price strings and converted with one rate fetch from frankfurter.app |
| Hosting | Render free web service from the Dockerfile (HF Docker Spaces became PRO-only in 2026); binds `$PORT`; env vars `OPENAI_API_KEY`, `SERPAPI_API_KEY` |

## Inputs

origin city, destination city, depart date, return date, travelers (int), budget (number), currency (USD/INR), preferences (free text).

## Architecture

```
web/ (React, 21st.dev components) ── EventSource GET /api/plan?… ──► api.py (FastAPI, SSE)
                                                                       │  calls plan(request, on_event); on_event → SSE events
                                                                       ▼
orchestrator.py                        specialists.py
   split budget 35/30/35 (flight / stay / experiences+dining)
   asyncio.gather ───► flight_agent   ── search_flights    (SerpApi google_flights)
                  ───► stay_agent     ── search_hotels     (SerpApi google_hotels)
                  ───► activity_agent ── search_sights (google top_sights) + search_places ×2-4 (google_maps)
   validate total; retry worst offender ≤2×
   writer_agent ◄── all three results ── markdown itinerary
   │
   ▼
db.py  save(request, itinerary, totals) / list() / load(id)
```

The orchestrator is deterministic Python, not an LLM. Specialist agents are LLM agents with tools and typed outputs. The writer agent is an LLM agent with no tools.

## Files

- `api.py` — FastAPI. `GET /api/plan?origin&destination&depart&return_date&travelers&budget&currency&preferences` streams `text/event-stream`: one `{kind, status, detail}` event per `on_event` call, then a final `{kind:"plan", status:"done", itinerary, totals, over_budget}` (or `status:"error"`). `GET /api/plans` lists saved plans, `GET /api/plans/{id}` returns one. Mounts `web/dist` as static files when it exists.
- `web/` — Vite + React + TypeScript + Tailwind + shadcn. `<meta name="referrer" content="no-referrer">` so Google photo CDN URLs load; broken images hide themselves. 21st.dev components fetched through the 21st.dev MCP connector: Booking Form (adapted to our inputs) and AI Task List (one task per agent; retries shown as subtasks). Itinerary rendered with `react-markdown`. Sidebar of past plans from `/api/plans`.
- `Dockerfile` — two-stage: node builds `web/dist`, python image runs `uvicorn api:app` on `$PORT` (default 8000).
- `orchestrator.py` — `async def plan(req: PlanRequest, on_event) -> PlanResult`. Budget split constants, gather, validate/retry, writer call. City names go straight to the agents; the flight agent converts them to IATA codes itself.
- `specialists.py` — `serpapi()` helper (urllib), `@function_tool`s, Pydantic output models, four `Agent` definitions. (Not `agents.py` — that name collides with the SDK package.)
- `db.py` — SQLite, one table `plans(id, created_at, request_json, itinerary_md, total, currency)`.
- `test_orchestrator.py` — budget retry logic with fake runners; no network.
- `requirements.txt`, `.env.example`, `README.md`.

## Specialist agents

Each gets: destination code/name, dates, travelers, its budget cap, currency, and the preferences text. Each has `output_type` set to a Pydantic model so the orchestrator can read `total` without parsing prose. Each is instructed to pick the option that best fits the preferences within the cap — quality and fit over cheapness, the cap is meant to be used — explain the pick in `reason`, and return an empty result only when the tool returned an error or nothing, never because options exceed the cap.

- **Flight agent** — tool `search_flights(origin_iata, destination_iata, depart, return_date, adults, currency)` → up to 10 outbound options with Google's round-trip total, airlines, times, stops, duration. Output `FlightPick{airline, depart_at, return_at, stops, total, image (airline logo), reason}`.
- **Stay agent** — tool `search_hotels(city, check_in, check_out, adults, currency, max_nightly)` → up to 15 hotels in Google's ranking under `max_nightly` (agent derives it from cap ÷ nights), with description, amenities, nearby places, photo. Output `StayPick{name, description, stars, rating, nightly, total, image, reason}`.
- **Experience agent** (key `activity`) — "knowledge proposes, search verifies": it first decides what a well-travelled local would recommend, then confirms via `search_sights(city, currency)` (Google top sights with listed prices, photos, links) and 2–4 targeted `search_places(query, city)` calls (Google Maps, results sorted best-first with rating, review count, address, compact opening hours, photo, `place_id` link). Quality floor ★4.3/300 reviews (never <4.0/<50 unless nothing else fits). Each day clustered in one `area`; `time_of_day` must be consistent with `hours`; each place at most once (`orchestrator.dedupe_items` enforces). Every non-departure day gets 1–2 sights/experiences and a dinner; nightlife on 1–2 evenings when asked. Output `ActivityPlan{items: [{name, kind: sight|experience|food|nightlife, description, price, estimated, rating, image, day, time_of_day}], total, estimated_total, currency, reason}`. Prices: listed when Google has one (`estimated=false`); otherwise a typical price flagged `estimated=true` — shown as `~X est.` and totalled under the cost table. Never invent a listed price.
- **Writer agent** — no tools, `output_type=Itinerary{title, overview, estimate_note, days: [{day, date, title, slots: [{time_of_day, notes: [str], items: [int]}]}]}`. Input: request, flight, stay, numbered experience items (without URLs). It owns timing (nothing before the outbound lands, nothing after check-out) and may move items between days. `orchestrator.reconcile()` then dedupes references and places any item the writer left out on the lightest non-travel day, and sorts slots. The React `Itinerary.tsx` renders: header + budget line, cost table + estimate note, flight/hotel cards, per day → per slot → note bullets + a row of place cards (photo or kind icon, name, `kind · ★rating · price/~est./free`, linking to Google Maps via `place_id` or the sight's Google link), Why these picks. The whole `plan` dict (request, totals, picks, itinerary) is the SSE payload and the SQLite row.

Model: `OPENAI_MODEL` env var, default `gpt-4.1-mini`; same model for all agents.

## Budget loop

```
caps = {flight: 0.35, stay: 0.30, activity: 0.35} × budget
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

- SerpApi errors → tool returns `{"error": ...}`, empty results → `[]`; agent reports "no options found"; itinerary marks that section unavailable. Prices are never fabricated.
- FX fetch failure → activity total left in source currency and flagged in the itinerary rather than guessed.
- Missing API keys → `/api/plan` returns HTTP 503 with the missing names; the UI shows that message.
- Input validation at the API boundary: return after depart, travelers 1–9, budget > 0, currency ∈ {USD, INR}; violations are HTTP 422.
- Google top-sights cards sometimes carry no price; unpriced sights are dropped so the activity total stays honest.

## Testing

`test_api.py` monkeypatches `plan` with a fake that emits two events and returns a fixed result, then asserts the SSE body contains those events in order followed by the final `plan/done` event, and that the plan was saved.

`test_orchestrator.py` monkeypatches the per-agent runner with fakes returning fixed totals and asserts:
1. total under budget → each agent runs once;
2. total over budget → worst offender re-runs with cap reduced by the overage;
3. still over after 2 retries → loop stops, `over_budget=True`.

No live OpenAI or SerpApi calls in tests.

## Out of scope

Token-level streaming of agent reasoning, configurable budget split, currencies beyond USD/INR, hosted DB, booking/checkout, multi-city trips, user accounts, dark/light theme toggle beyond what the 21st.dev components ship with.
