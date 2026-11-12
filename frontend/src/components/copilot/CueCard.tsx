import { AnswerMarkdown } from "@/components/AnswerMarkdown";
import { cn } from "@/components/ui/cn";
import type { QA } from "@/hooks/useCopilotSession";

const CATEGORY: Record<string, string> = { behavioral: "Behavioral", technical: "Technical", other: "General" };

/** The answer, written on a light card so it is the one thing the eye goes to. */
export function CueCard({ qa, size = "lg" }: { qa: QA; size?: "lg" | "xl" | "sm" }) {
  const textSize = { sm: "text-sm", lg: "text-lg leading-relaxed", xl: "text-xl leading-relaxed" }[size];
  return (
    <article className="rounded-card bg-paper p-5 text-paper-ink sm:p-6" aria-live="polite" aria-busy={qa.streaming}>
      <header className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-paper-muted">
        <span className="rounded-full bg-paper-ink px-2 py-0.5 font-semibold text-paper">
          {CATEGORY[qa.category] ?? qa.category}
        </span>
        {qa.timings?.first_token_ms != null && (
          <span>First words in {(qa.timings.first_token_ms / 1000).toFixed(1)} s</span>
        )}
        {qa.stopped && <span>Stopped</span>}
      </header>
      <h2 className={cn("mb-3 font-semibold text-paper-muted", size === "sm" ? "text-sm" : "text-base")}>{qa.question}</h2>
      <div className={textSize}>
        {qa.answer ? (
          <AnswerMarkdown text={qa.answer} />
        ) : qa.streaming ? (
          <p className="text-paper-muted">Writing your answer...</p>
        ) : null}
        {qa.streaming && qa.answer && <span aria-hidden className="tally ml-0.5 inline-block h-[1em] w-[2px] translate-y-[3px] bg-paper-ink" />}
      </div>
      {!qa.streaming && qa.unverified && qa.unverified.length > 0 && (
        <p className="mt-4 rounded-control bg-warn/25 px-3 py-2 text-sm font-semibold">
          Check before saying: {qa.unverified.join(", ")} {qa.unverified.length === 1 ? "is" : "are"} not in your CV.
        </p>
      )}
    </article>
  );
}
