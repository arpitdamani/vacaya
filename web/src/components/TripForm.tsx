// Adapted from "Booking Form" by lavikatiyar — https://21st.dev/@lavikatiyar/components/form
// (fetched via the 21st.dev MCP connector). Same card, icon-in-field and staggered-motion
// styling; fields changed from destination/dates/rooms/guests to a full trip request.
import { useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { CalendarDays, MapPin, Plane, Sparkles, Users2, Wallet } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { TripInput } from "@/lib/api";

const plusDays = (n: number) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);

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

export function TripForm({ onSubmit, disabled }: { onSubmit: (t: TripInput) => void; disabled: boolean }) {
  const [t, set] = useState<TripInput>({
    origin: "Madrid", destination: "Paris", depart: plusDays(30), return_date: plusDays(34),
    travelers: 1, budget: 2000, currency: "USD", preferences: "",
  });
  const upd = <K extends keyof TripInput>(k: K, v: TripInput[K]) => set((s) => ({ ...s, [k]: v }));
  const invalid = t.return_date <= t.depart || !(t.budget > 0) || !t.origin.trim() || !t.destination.trim();

  return (
    <motion.div variants={containerVariants} initial="hidden" animate="visible" className="w-full rounded-2xl bg-card p-6 shadow-lg">
      <form onSubmit={(e) => { e.preventDefault(); if (!invalid) onSubmit(t); }} className="space-y-6">
        <motion.div variants={itemVariants} className="space-y-2">
          <h3 className="font-medium text-card-foreground">Route</h3>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Field icon={<Plane />} label="From">
              <input className={FIELD} value={t.origin} onChange={(e) => upd("origin", e.target.value)} placeholder="From: Madrid" required />
            </Field>
            <Field icon={<MapPin />} label="To">
              <input className={FIELD} value={t.destination} onChange={(e) => upd("destination", e.target.value)} placeholder="To: Paris" required />
            </Field>
          </div>
        </motion.div>

        <motion.div variants={itemVariants} className="space-y-2">
          <h3 className="font-medium text-card-foreground">Dates &amp; travelers</h3>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Field icon={<CalendarDays />} label="Depart">
              <input type="date" className={FIELD} value={t.depart} onChange={(e) => upd("depart", e.target.value)} required />
            </Field>
            <Field icon={<CalendarDays />} label="Return">
              <input type="date" className={FIELD} value={t.return_date} min={t.depart} onChange={(e) => upd("return_date", e.target.value)} required />
            </Field>
            <Field icon={<Users2 />} label="Travelers" className="sm:max-w-32">
              <input type="number" min={1} max={9} className={FIELD} value={t.travelers} onChange={(e) => upd("travelers", Number(e.target.value))} required />
            </Field>
          </div>
          {t.return_date <= t.depart && <p className="text-sm text-destructive">Return must be after departure.</p>}
        </motion.div>

        <motion.div variants={itemVariants} className="space-y-2">
          <h3 className="font-medium text-card-foreground">Budget</h3>
          <div className="flex gap-2">
            <Field icon={<Wallet />} label="Total budget">
              <input type="number" min={1} step="any" className={FIELD} value={t.budget} onChange={(e) => upd("budget", Number(e.target.value))} required />
            </Field>
            <select
              aria-label="Currency"
              className="h-12 rounded-xl border border-input bg-transparent px-4 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={t.currency}
              onChange={(e) => upd("currency", e.target.value as TripInput["currency"])}
            >
              <option value="USD">USD</option>
              <option value="INR">INR</option>
            </select>
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
