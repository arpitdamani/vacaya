import { useEffect, useRef, useState } from "react";
import { History } from "lucide-react";
import { TripForm } from "@/components/TripForm";
import { AgentProgress } from "@/components/AgentProgress";
import { ItineraryView } from "@/components/Itinerary";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { listPlans, loadPlan, streamPlan, type Plan, type ProgressEvent, type SavedPlan, type TripInput } from "@/lib/api";

type Phase = "idle" | "running" | "done" | "error";

export default function App() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<SavedPlan[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);

  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    listPlans().then(setSaved).catch(() => {});
    if (phase !== "idle") mainRef.current?.scrollIntoView({ behavior: "smooth" });
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
    setPlan(plan); setEvents([]); setPhase("done"); setPanelOpen(false);
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <section className="relative flex min-h-dvh items-center justify-center bg-[url('/hero.jpg')] bg-cover bg-center p-4 py-16 md:p-8 print:hidden">
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-48 bg-linear-to-t from-background to-transparent" />
        <div className="relative w-full max-w-2xl rounded-2xl bg-card/95 p-6 shadow-xl backdrop-blur md:p-8">
          <div className="mb-6 flex items-start justify-between gap-4">
            <h1 className="text-3xl font-bold leading-tight">
              Vacaya <span className="block text-base font-normal text-muted-foreground sm:inline sm:ml-2">Your Personal Travel Planner</span>
            </h1>
            <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
              <SheetTrigger render={<Button variant="outline" />}>
                <History /> Past plans{saved.length > 0 && <span className="text-muted-foreground">({saved.length})</span>}
              </SheetTrigger>
              <SheetContent side="left">
                <SheetHeader>
                  <SheetTitle>Past plans</SheetTitle>
                  <SheetDescription>Plans made on this server. Click one to open it.</SheetDescription>
                </SheetHeader>
                <ul className="space-y-1 overflow-y-auto px-4 pb-4">
                  {saved.length === 0 && <li className="text-sm text-muted-foreground">Nothing yet.</li>}
                  {saved.map((p) => (
                    <li key={p.id}>
                      <button onClick={() => open(p)} className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted">
                        <div className="font-medium">{p.title}</div>
                        <div className="text-xs text-muted-foreground">{p.total.toLocaleString()} {p.currency} · {p.created_at.slice(0, 10)}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              </SheetContent>
            </Sheet>
          </div>
          <TripForm onSubmit={start} disabled={phase === "running"} />
        </div>
      </section>
      <div className="mx-auto max-w-5xl p-4 md:p-8">
        <main ref={mainRef} className="space-y-6">
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
