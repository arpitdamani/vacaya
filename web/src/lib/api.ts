export type TripInput = {
  origin: string; destination: string; depart: string; return_date: string;
  travelers: number; budget: number; currency: "USD" | "INR"; preferences: string;
};
export type AgentKind = "flight" | "stay" | "activity" | "writer";
export type ProgressEvent = { kind: AgentKind; status: "start" | "done" | "retry"; detail: string };

export type FlightPick = { airline: string; depart_at: string; return_at: string; stops: number; total: number; image: string; reason: string };
export type StayPick = { name: string; description: string; stars: string; rating: number; nightly: number; total: number; image: string; reason: string };
export type ItemKind = "sight" | "experience" | "food" | "nightlife" | "shopping";
export type ActivityItem = {
  name: string; kind: ItemKind; description: string; price: number; estimated: boolean; rating: number;
  area: string; hours: string; image: string; link: string; day: number; time_of_day: "morning" | "afternoon" | "evening";
};
export type ActivityPlan = { items: ActivityItem[]; total: number; estimated_total: number; currency: string; reason: string };
export type Slot = { time_of_day: "morning" | "afternoon" | "evening"; notes: string[]; items: number[] };
export type Day = { day: number; date: string; title: string; slots: Slot[] };
export type Itinerary = { title: string; overview: string; days: Day[] };
export type Plan = {
  request: TripInput; totals: Record<string, number>; caps: Record<string, number>; over_budget: boolean;
  flight: FlightPick; stay: StayPick; activity: ActivityPlan; itinerary: Itinerary;
};

export type PlanDone = { kind: "plan"; status: "done"; id: number; plan: Plan };
export type PlanError = { kind: "plan"; status: "error"; detail: string };
export type SseEvent = ProgressEvent | PlanDone | PlanError;
export type SavedPlan = { id: number; created_at: string; title: string; total: number; currency: string };

export function streamPlan(input: TripInput, onEvent: (e: SseEvent) => void, onFail: (msg: string) => void) {
  const qs = new URLSearchParams(Object.entries(input).map(([k, v]) => [k, String(v)]));
  const es = new EventSource(`/api/plan?${qs}`);
  let received = false;
  es.onmessage = (m) => {
    received = true;
    const ev = JSON.parse(m.data) as SseEvent;
    onEvent(ev);
    if (ev.kind === "plan") es.close();
  };
  es.onerror = async () => {
    es.close();
    if (received) return onFail("Connection lost");
    // EventSource hides HTTP status; re-fetch to read the 4xx/5xx body. Safe only before any
    // event arrived: a 4xx/5xx is raised before the plan starts, so this never runs the agents twice.
    const r = await fetch(`/api/plan?${qs}`).catch(() => null);
    onFail(r && !r.ok ? ((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`) : "Connection lost");
  };
  return () => es.close();
}

export const listPlans = () => fetch("/api/plans").then((r) => r.json() as Promise<SavedPlan[]>);
export const loadPlan = (id: number) => fetch(`/api/plans/${id}`).then((r) => r.json() as Promise<{ plan: Plan }>);
