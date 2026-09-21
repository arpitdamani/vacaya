"""FastAPI front door: streams orchestrator progress over SSE and serves the built React app."""
import asyncio
import json
import os
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

import db
from orchestrator import PlanRequest, plan

load_dotenv()
app = FastAPI(title="Vacaya")

REQUIRED_KEYS = ("OPENAI_API_KEY", "SERPAPI_API_KEY")


@app.get("/api/plans")
def list_plans():
    return [dict(zip(("id", "created_at", "title", "total", "currency"), row)) for row in db.list_plans()]


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int):
    return {"itinerary": db.load(plan_id)}


@app.get("/api/plan")
async def stream_plan(
    origin: str = Query(min_length=2),
    destination: str = Query(min_length=2),
    depart: date = Query(),
    return_date: date = Query(),
    travelers: int = Query(1, ge=1, le=9),
    budget: float = Query(gt=0),
    currency: Literal["USD", "INR"] = "USD",
    preferences: str = "",
):
    if return_date <= depart:
        raise HTTPException(422, "return_date must be after depart")
    missing = [k for k in REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise HTTPException(503, f"Missing secrets: {', '.join(missing)}")

    req = PlanRequest(origin, destination, depart, return_date, travelers, budget, currency, preferences)
    queue: asyncio.Queue = asyncio.Queue()

    async def work():
        try:
            result = await plan(req, lambda kind, status, detail: queue.put_nowait({"kind": kind, "status": status, "detail": detail}))
            total = sum(result.totals.values())
            pid = db.save(f"{origin} → {destination} {depart}", asdict(req), result.itinerary_md, total, currency)
            queue.put_nowait({"kind": "plan", "status": "done", "id": pid, "itinerary": result.itinerary_md,
                              "totals": result.totals, "over_budget": result.over_budget})
        except Exception as e:  # surfaced to the client as the terminal event
            queue.put_nowait({"kind": "plan", "status": "error", "detail": str(e)})

    async def events():
        task = asyncio.create_task(work())
        while True:
            ev = await queue.get()
            yield f"data: {json.dumps(ev)}\n\n"
            if ev["kind"] == "plan":
                break
        await task

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


dist = Path(__file__).with_name("web") / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
