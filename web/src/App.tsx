import { useEffect, useState } from "react";
import { TripForm } from "@/components/TripForm";
import { AgentProgress } from "@/components/AgentProgress";
import { ItineraryView } from "@/components/Itinerary";
import { listPlans, loadPlan, streamPlan, type Plan, type ProgressEvent, type SavedPlan, type TripInput } from "@/lib/api";

type Phase = "idle" | "running" | "done" | "error";

export default function App() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<SavedPlan[]>([]);

  useEffect(() => {
    listPlans().then(setSaved).catch(() => {});
  }, [phase]);

  function start(t: TripInput) {
    setPhase("running"); setEvents([]); setPlan(null); setError("");
    streamPlan(t, (ev) => {
      if (ev.kind !== "plan") return setEvents((es) => [...es, ev]);
      if (ev.status === "done") { setPlan(ev.plan); setPhase("done"); }
      else { setError(ev.detail); setPhase("error"); }
    }, (msg) => { setError(msg); setPhase("error"); });
  }

  async function open(p: SavedPlan) {
    const { plan } = await loadPlan(p.id);
    setPlan(plan); setEvents([]); setPhase("done");
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto grid max-w-6xl gap-6 p-4 md:grid-cols-[260px_1fr] md:p-8">
        <aside className="print:hidden">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Past plans</h2>
          {saved.length === 0 && <p className="text-sm text-muted-foreground">Nothing yet.</p>}
          <ul className="space-y-1">
            {saved.map((p) => (
              <li key={p.id}>
                <button onClick={() => open(p)} className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted">
                  {p.title} · {p.total.toLocaleString()} {p.currency}
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <main className="space-y-6">
          <header className="print:hidden">
            <h1 className="text-3xl font-bold">Vacaya</h1>
            <p className="text-muted-foreground">Three specialist agents, one budget, one itinerary.</p>
          </header>
          <div className="print:hidden"><TripForm onSubmit={start} disabled={phase === "running"} /></div>
          {(phase === "running" || events.length > 0) && <div className="print:hidden"><AgentProgress events={events} failed={phase === "error"} /></div>}
          {phase === "error" && <p role="alert" className="rounded-md border border-destructive p-3 text-destructive">{error}</p>}
          {plan && (
            <section className="space-y-4">
              <ItineraryView plan={plan} />
              <button onClick={() => window.print()} className="text-sm underline print:hidden">Print / save as PDF</button>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
