"use client";

import { useEffect, useRef } from "react";
import type { Line, Speaker } from "@/hooks/useCopilotSession";

const LABEL: Record<Speaker, string> = { interviewer: "Interviewer", candidate: "You" };

function TranscriptLine({ speaker, text, pending }: { speaker: Speaker; text: string; pending?: boolean }) {
  return (
    <p className={pending ? "text-muted" : undefined}>
      <span className={speaker === "interviewer" ? "mr-2 font-semibold text-accent" : "mr-2 font-semibold text-muted"}>
        {LABEL[speaker]}
      </span>
      {text}
    </p>
  );
}

export function TranscriptPanel({
  lines,
  partial,
  emptyText,
}: {
  lines: Line[];
  partial: Partial<Record<Speaker, string>>;
  emptyText: string;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [lines, partial]);
  const empty = lines.length === 0 && !partial.interviewer && !partial.candidate;
  return (
    <section aria-label="Transcript" className="flex min-h-0 flex-col rounded-panel border border-line">
      <h2 className="border-b border-line px-4 py-2.5 text-sm font-semibold text-muted">Transcript</h2>
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3 text-sm leading-relaxed">
        {empty && <p className="text-muted">{emptyText}</p>}
        {lines.map((l) => (
          <TranscriptLine key={l.id} speaker={l.speaker} text={l.text} />
        ))}
        {(["interviewer", "candidate"] as Speaker[]).map((s) =>
          partial[s] ? <TranscriptLine key={`p-${s}`} speaker={s} text={partial[s]!} pending /> : null,
        )}
      </div>
    </section>
  );
}
