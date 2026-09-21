// Adapted from "Booking Form" by lavikatiyar — https://21st.dev/@lavikatiyar/components/form
// (fetched via the 21st.dev MCP connector). Same card, icon-in-field and staggered-motion
// styling; fields changed from destination/dates/rooms/guests to a full trip request.
import { useMemo, useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { CalendarDays, MapPin, Plane, Sparkles, Users2, Wallet } from "lucide-react";
import { Combobox as ComboboxPrimitive } from "@base-ui/react";
import { Button } from "@/components/ui/button";
import { Combobox, ComboboxCollection, ComboboxContent, ComboboxEmpty, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { TripInput } from "@/lib/api";
import cities from "@/lib/cities.json";

const containerVariants = { hidden: { opacity: 0, y: 20 }, visible: { opacity: 1, y: 0, transition: { staggerChildren: 0.1 } } };
const itemVariants = { hidden: { opacity: 0, y: 10 }, visible: { opacity: 1, y: 0 } };

const FIELD =
  "h-12 w-full rounded-xl border border-input bg-transparent pl-10 pr-4 text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ring-offset-background";

function Field({ icon, label, className, children }: { icon: ReactNode; label: string; className?: string; children: ReactNode }) {
  return (
    <label className={cn("relative block flex-1", className)}>
      <span className="sr-only">{label}</span>
      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground [&>svg]:h-5 [&>svg]:w-5">{icon}</span>
      {children}
    </label>
  );
}

/** Form state: number fields may be empty while the user is still typing. */
type Draft = Omit<TripInput, "travelers" | "budget"> & { travelers: number | ""; budget: number | "" };

/** Up to 8 cities matching q: city-name prefix matches first (shortest name first, so "Tok" → Tokyo before Tokoname), then any substring match. */
function suggest(q: string): string[] {
  q = q.trim().toLowerCase();
  if (!q) return [];
  const hits = cities.filter((c) => c.toLowerCase().includes(q));
  const prefix = hits.filter((c) => c.toLowerCase().startsWith(q)).sort((a, b) => a.indexOf(",") - b.indexOf(","));
  return [...prefix, ...hits.filter((c) => !prefix.includes(c))].slice(0, 8);
}

/** Free-text city input with shadcn/base-ui suggestions from the airport-city list. */
function CityField({ icon, label, placeholder, value, onChange }: { icon: ReactNode; label: string; placeholder: string; value: string; onChange: (v: string) => void }) {
  const filteredItems = useMemo(() => suggest(value), [value]);
  return (
    <Combobox<string> items={cities} filteredItems={filteredItems} openOnInputClick={false} inputValue={value} onInputValueChange={onChange}
              onValueChange={(v: string | null) => v && onChange(v)}>
      <Field icon={icon} label={label}>
        <ComboboxPrimitive.Input className={FIELD} placeholder={placeholder} required />
      </Field>
      <ComboboxContent>
        <ComboboxEmpty>No matching city — any place name works.</ComboboxEmpty>
        <ComboboxList>
          <ComboboxCollection>{(c: string) => <ComboboxItem key={c} value={c}>{c}</ComboboxItem>}</ComboboxCollection>
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}

export function TripForm({ onSubmit, disabled }: { onSubmit: (t: TripInput) => void; disabled: boolean }) {
  const [t, set] = useState<Draft>({ origin: "", destination: "", depart: "", return_date: "", travelers: "", budget: "", currency: "USD", preferences: "" });
  const upd = <K extends keyof Draft>(k: K, v: Draft[K]) => set((s) => ({ ...s, [k]: v }));
  const num = (v: string) => (v === "" ? "" : Number(v));
  const datesReversed = !!t.depart && !!t.return_date && t.return_date <= t.depart;
  const invalid = !t.origin.trim() || !t.destination.trim() || !t.depart || !t.return_date || datesReversed || !(Number(t.travelers) >= 1) || !(Number(t.budget) > 0);

  return (
    <motion.div variants={containerVariants} initial="hidden" animate="visible" className="w-full">
      <form onSubmit={(e) => { e.preventDefault(); if (!invalid) onSubmit({ ...t, travelers: Number(t.travelers), budget: Number(t.budget) }); }} className="space-y-6">
        <motion.div variants={itemVariants} className="space-y-2">
          <h3 className="font-medium text-card-foreground">Route</h3>
          <div className="flex flex-col gap-2 sm:flex-row">
            <CityField icon={<Plane />} label="From" placeholder="Departure city" value={t.origin} onChange={(v) => upd("origin", v)} />
            <CityField icon={<MapPin />} label="To" placeholder="Arrival destination" value={t.destination} onChange={(v) => upd("destination", v)} />
          </div>
        </motion.div>

        <motion.div variants={itemVariants} className="space-y-2">
          <h3 className="font-medium text-card-foreground">Dates &amp; travelers</h3>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Field icon={<CalendarDays />} label="Depart">
              <input type="date" className={FIELD} value={t.depart} onChange={(e) => upd("depart", e.target.value)} placeholder="Departure date" data-empty={!t.depart || undefined} required />
            </Field>
            <Field icon={<CalendarDays />} label="Return">
              <input type="date" className={FIELD} value={t.return_date} min={t.depart} onChange={(e) => upd("return_date", e.target.value)} placeholder="Return date" data-empty={!t.return_date || undefined} required />
            </Field>
            <Field icon={<Users2 />} label="Travelers" className="sm:max-w-40">
              <input type="number" min={1} max={9} className={FIELD} value={t.travelers} onChange={(e) => upd("travelers", num(e.target.value))} placeholder="No. of pax" required />
            </Field>
          </div>
          {datesReversed && <p className="text-sm text-destructive">Return must be after departure.</p>}
        </motion.div>

        <motion.div variants={itemVariants} className="space-y-2">
          <h3 className="font-medium text-card-foreground">Budget</h3>
          <div className="flex gap-2">
            <Field icon={<Wallet />} label="Total budget">
              <input type="number" min={1} step="any" className={FIELD} value={t.budget} onChange={(e) => upd("budget", num(e.target.value))} placeholder="Total budget" required />
            </Field>
            <Select value={t.currency} onValueChange={(v) => upd("currency", v as TripInput["currency"])}>
              <SelectTrigger aria-label="Currency" className="h-12 rounded-xl px-4 text-base data-[size=default]:h-12">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="USD">USD</SelectItem>
                <SelectItem value="INR">INR</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </motion.div>

        <motion.div variants={itemVariants} className="space-y-2">
          <h3 className="font-medium text-card-foreground">Preferences</h3>
          <Field icon={<Sparkles />} label="Preferences">
            <textarea
              rows={2}
              className={cn(FIELD, "h-auto min-h-20 py-3")}
              value={t.preferences}
              onChange={(e) => upd("preferences", e.target.value)}
              placeholder="non-stop flights, boutique hotel near the centre, museums, vegetarian food, no nightlife"
            />
          </Field>
        </motion.div>

        <motion.div variants={itemVariants} whileTap={{ scale: 0.98 }}>
          <Button type="submit" disabled={disabled || invalid} className="h-12 w-full rounded-xl text-base font-bold">
            {disabled ? "Planning…" : "Plan my trip"}
          </Button>
        </motion.div>
      </form>
    </motion.div>
  );
}
