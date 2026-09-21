import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { TripForm } from "@/components/TripForm";
import { AgentProgress } from "@/components/AgentProgress";
import { listPlans, loadPlan, streamPlan, type PlanDone, type ProgressEvent, type SavedPlan, type TripInput } from "@/lib/api";

type Phase = "idle" | "running" | "done" | "error";

export default function App() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [result, setResult] = useState<PlanDone | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<SavedPlan[]>([]);
  const [currency, setCurrency] = useState("USD");

  useEffect(() => {
    listPlans().then(setSaved).catch(() => {});
  }, [phase]);

  function start(t: TripInput) {
    setPhase("running"); setEvents([]); setResult(null); setError(""); setCurrency(t.currency);
    streamPlan(t, (ev) => {
      if (ev.kind !== "plan") return setEvents((es) => [...es, ev]);
      if (ev.status === "done") { setResult(ev); setPhase("done"); }
      else { setError(ev.detail); setPhase("error"); }
    }, (msg) => { setError(msg); setPhase("error"); });
  }

  async function open(p: SavedPlan) {
    const { itinerary } = await loadPlan(p.id);
    setResult({ kind: "plan", status: "done", id: p.id, itinerary, totals: {}, over_budget: false });
    setCurrency(p.currency); setEvents([]); setPhase("done");
  }

  const total = result ? Object.values(result.totals).reduce((a, b) => a + b, 0) : 0;
  const hasTotals = !!result && Object.keys(result.totals).length > 0;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto grid max-w-6xl gap-6 p-4 md:grid-cols-[260px_1fr] md:p-8">
        <aside>
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
          <header>
            <h1 className="text-3xl font-bold">Vacaya</h1>
            <p className="text-muted-foreground">Three specialist agents, one budget, one itinerary.</p>
          </header>
          <TripForm onSubmit={start} disabled={phase === "running"} />
          {(phase === "running" || events.length > 0) && <AgentProgress events={events} failed={phase === "error"} />}
          {phase === "error" && <p role="alert" className="rounded-md border border-destructive p-3 text-destructive">{error}</p>}
          {result && (
            <section className="space-y-3">
              {hasTotals && (
                <p className={result.over_budget ? "font-medium text-destructive" : "font-medium text-green-600"}>
                  {result.over_budget ? "Over budget after retries" : "Within budget"}: {total.toLocaleString()} {currency}
                </p>
              )}
              <article className="prose prose-neutral max-w-none dark:prose-invert">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    // photos come from Google/SerpApi CDNs and occasionally 400; hide rather than show alt text
                    img: ({ src, alt }) => (
                      <img src={src} alt={alt ?? ""} loading="lazy" onError={(e) => { e.currentTarget.style.display = "none"; }} />
                    ),
                  }}
                >
                  {result.itinerary}
                </ReactMarkdown>
              </article>
              <a className="inline-block text-sm underline" download="itinerary.md"
                 href={`data:text/markdown;charset=utf-8,${encodeURIComponent(result.itinerary)}`}>Download itinerary (.md)</a>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
