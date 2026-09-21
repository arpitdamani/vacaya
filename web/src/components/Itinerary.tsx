import type { ActivityItem, ItemKind, Plan } from "@/lib/api";

const KIND_ICON: Record<ItemKind, string> = { sight: "🏛️", experience: "🎟️", food: "🍽️", nightlife: "🎶", shopping: "🛍️" };
const SLOT_LABEL = { morning: "Morning", afternoon: "Afternoon", evening: "Evening" } as const;

const money = (n: number, currency: string) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(n);

function priceLabel(it: ActivityItem, currency: string) {
  const base = it.price === 0 ? "free" : money(it.price, currency);
  return it.estimated ? `~${base}${it.price === 0 ? "" : " est."}` : base;
}

function longDate(iso: string) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

/** One card: photo, name, "kind · ★rating · price". Links to the Google page when we have one. */
function Card({ image, name, meta, sub, link, icon, wide = false }: { image: string; name: string; meta: string; sub?: string; link?: string; icon: string; wide?: boolean }) {
  const inner = (
    <>
      <div className={`${wide ? "h-40" : "h-32"} w-full overflow-hidden bg-muted`}>
        {image ? (
          <img src={image} alt="" loading="lazy" className="h-full w-full object-cover"
               onError={(e) => { e.currentTarget.style.display = "none"; }} />
        ) : (
          <div className="flex h-full items-center justify-center text-4xl">{icon}</div>
        )}
      </div>
      <div className="border-t border-border p-3">
        <div className="line-clamp-2 font-medium leading-tight" title={name}>{name}</div>
        <div className="mt-1 text-xs text-muted-foreground">{meta}</div>
        {sub && <div className="mt-0.5 truncate text-[11px] text-muted-foreground/80" title={sub}>{sub}</div>}
      </div>
    </>
  );
  const cls = `${wide ? "w-full sm:w-72" : "w-full sm:w-52"} shrink-0 overflow-hidden rounded-2xl border border-border bg-card text-card-foreground shadow-sm transition hover:shadow-md`;
  return link ? <a href={link} target="_blank" rel="noopener noreferrer" className={cls}>{inner}</a> : <div className={cls}>{inner}</div>;
}

export function ItineraryView({ plan }: { plan: Plan }) {
  const { request: req, totals, flight, stay, activity, itinerary } = plan;
  const cur = req.currency;
  const total = Object.values(totals).reduce((a, b) => a + b, 0);
  const diff = req.budget - total;

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="font-heading text-3xl sm:text-4xl">{itinerary.title}</h1>
        <p className="text-muted-foreground">{itinerary.overview}</p>
        <p className={`font-medium ${plan.over_budget ? "text-destructive" : "text-green-600"}`}>
          {plan.over_budget ? `Over budget by ${money(-diff, cur)}` : `Within budget — ${money(diff, cur)} to spare`}
        </p>
      </header>

      <section>
        <h2 className="mb-2 text-xl font-semibold">Cost</h2>
        <table className="w-full max-w-md text-sm">
          <tbody>
            {[["Flights", totals.flight], ["Stay", totals.stay], ["Experiences & dining", totals.activity]].map(([k, v]) => (
              <tr key={k as string} className="border-b border-border"><td className="py-1.5">{k}</td><td className="py-1.5 text-right tabular-nums">{money(v as number, cur)}</td></tr>
            ))}
            <tr className="border-b border-border font-semibold"><td className="py-1.5">Total</td><td className="py-1.5 text-right tabular-nums">{money(total, cur)}</td></tr>
            <tr className="border-b border-border"><td className="py-1.5">Budget</td><td className="py-1.5 text-right tabular-nums">{money(req.budget, cur)}</td></tr>
          </tbody>
        </table>
        {activity.estimated_total > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            ~{money(activity.estimated_total, cur)} of the experiences figure is a typical-price estimate, not a quote.
          </p>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-xl font-semibold">Getting there &amp; staying</h2>
        <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap">
          {flight.total > 0 ? (
            <Card wide icon="✈️" image={flight.image} name={`${flight.airline} — ${req.origin} → ${req.destination}`}
                  meta={`${flight.stops === 0 ? "non-stop" : `${flight.stops} stop${flight.stops > 1 ? "s" : ""}`} · out ${flight.depart_at} · back ${flight.return_at} · ${money(flight.total, cur)} round trip`} />
          ) : <p className="text-sm text-muted-foreground">No flights found — {flight.reason}</p>}
          {stay.total > 0 ? (
            <Card wide icon="🏨" image={stay.image} name={stay.name}
                  meta={`${stay.stars || "hotel"} · ★${stay.rating} · ${money(stay.nightly, cur)}/night · ${money(stay.total, cur)} total`} />
          ) : <p className="text-sm text-muted-foreground">No hotel found — {stay.reason}</p>}
        </div>
        {stay.total > 0 && <p className="mt-3 max-w-2xl text-sm text-muted-foreground">{stay.description}</p>}
      </section>

      {itinerary.days.map((d) => (
        <section key={d.day} className="space-y-4">
          <h2 className="font-heading text-3xl">Day {d.day} – {longDate(d.date)} <span className="block text-2xl italic text-muted-foreground sm:ml-2 sm:inline">{d.title}</span></h2>
          {d.slots.map((s) => {
            const items = s.items.map((i) => activity.items[i]).filter(Boolean);
            return (
              <div key={s.time_of_day} className="space-y-3">
                <h3 className="text-lg font-medium">{SLOT_LABEL[s.time_of_day]}</h3>
                {s.notes.length > 0 && (
                  <ul className="list-disc space-y-1 pl-5 text-sm">
                    {s.notes.map((n, i) => <li key={i}>{n}</li>)}
                  </ul>
                )}
                {items.length > 0 && (
                  <div className="grid grid-cols-2 gap-3 sm:flex sm:flex-wrap sm:gap-4">
                    {items.map((it) => (
                      <Card key={it.name} image={it.image} name={it.name} link={it.link || undefined} icon={KIND_ICON[it.kind]}
                            meta={`${it.kind}${it.area ? ` · ${it.area}` : ""} · ${it.rating ? `★${it.rating} · ` : ""}${priceLabel(it, cur)}`}
                            sub={it.hours || undefined} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </section>
      ))}

      <section>
        <h2 className="mb-2 text-xl font-semibold">Why these picks</h2>
        <ul className="space-y-2 text-sm text-muted-foreground">
          <li><span className="font-medium text-foreground">Flights:</span> {flight.reason}</li>
          <li><span className="font-medium text-foreground">Stay:</span> {stay.reason}</li>
          <li><span className="font-medium text-foreground">Experiences:</span> {activity.reason}</li>
        </ul>
      </section>
    </div>
  );
}
