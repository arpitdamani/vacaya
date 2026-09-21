import AITaskList, { type AITask, type AITaskStatus } from "@/components/ui/ai-task-list";
import type { AgentKind, ProgressEvent } from "@/lib/api";

const LABEL: Record<AgentKind, string> = {
  flight: "Flight agent — round-trip flights",
  stay: "Stay agent — hotel for the whole trip",
  activity: "Experience agent — sights, activities, dining & nightlife",
  writer: "Itinerary writer — day-by-day plan",
};

export function toTasks(events: ProgressEvent[], failed: boolean): AITask[] {
  return (Object.keys(LABEL) as AgentKind[]).map((kind) => {
    const mine = events.filter((e) => e.kind === kind);
    const last = mine.at(-1);
    const status: AITaskStatus =
      failed && last?.status !== "done" ? "failed" : !last ? "pending" : last.status === "done" ? "done" : "running";
    const note = last?.status === "done" ? last.detail : last?.status === "start" ? last.detail : undefined;
    const children = mine
      .filter((e) => e.status === "retry")
      .map((e, i) => ({ id: `${kind}-retry-${i}`, label: `Budget check: ${e.detail}`, status: "done" as AITaskStatus }));
    return { id: kind, label: LABEL[kind], note: note || undefined, status, children: children.length ? children : undefined };
  });
}

export function AgentProgress({ events, failed }: { events: ProgressEvent[]; failed: boolean }) {
  return <AITaskList label="Planning your trip" tasks={toTasks(events, failed)} />;
}
