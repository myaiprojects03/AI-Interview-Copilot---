"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { TopBar } from "@/components/AppShell";
import { CueCard } from "@/components/copilot/CueCard";
import { TranscriptPanel } from "@/components/copilot/TranscriptPanel";
import { SessionSetup, SetupChoice } from "@/components/SessionSetup";
import { Button } from "@/components/ui/Button";
import { cn } from "@/components/ui/cn";
import { Notice } from "@/components/ui/Field";
import { Kbd } from "@/components/ui/Misc";
import { Status, useCopilotSession } from "@/hooks/useCopilotSession";
import { Usage, useApi } from "@/lib/api";

function minutesLeft(seconds: number | null) {
  if (seconds == null) return "Unlimited minutes";
  const m = Math.floor(seconds / 60);
  return m < 1 ? "Less than a minute left" : `${m} min left this month`;
}

function LiveStatus({ status, paused, remaining }: { status: Status; paused: boolean; remaining: number | null }) {
  if (status === "connecting") {
    return (
      <span className="flex items-center gap-2 text-sm font-semibold" role="status">
        <span aria-hidden className="tally h-2.5 w-2.5 rounded-full bg-muted" />
        Connecting
      </span>
    );
  }
  if (status !== "live" && status !== "reconnecting") return null;
  const reconnecting = status === "reconnecting";
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="flex items-center gap-2 font-semibold" role="status">
        <span
          aria-hidden
          className={cn("h-2.5 w-2.5 rounded-full", reconnecting ? "tally bg-warn" : paused ? "bg-muted" : "tally bg-live")}
        />
        {reconnecting ? "Reconnecting" : paused ? "Paused" : "Live"}
      </span>
      <span className={cn("hidden text-muted sm:inline", remaining != null && remaining <= 120 && "font-semibold text-warn")}>
        {minutesLeft(remaining)}
      </span>
    </div>
  );
}

export default function CopilotPage() {
  const { api, token } = useApi();
  const s = useCopilotSession(token);
  const [focus, setFocus] = useState(false);
  const [usage, setUsage] = useState<Usage | null>(null);
  const setupRef = useRef<SetupChoice>({ cvId: null, jdId: null, sessionType: "mixed" });
  const [hasCv, setHasCv] = useState(false);
  const onSetupChange = useCallback((c: SetupChoice) => {
    setupRef.current = c;
    setHasCv(!!c.cvId);
  }, []);

  const live = s.status === "live";
  const idle = s.status === "idle";
  // Reconnecting keeps the interview layout as it was, so nothing jumps while the connection recovers.
  const inInterview = live || s.status === "reconnecting";
  const current = s.qas[s.qas.length - 1];
  const previous = s.qas.slice(0, -1).reverse();

  // Minutes left, shown before starting (refreshed after each session).
  useEffect(() => {
    if (token && idle) api.usage().then(setUsage).catch(() => setUsage(null));
  }, [api, token, idle]);

  // Keyboard shortcuts while live: R regenerate, S stop the answer, P pause, F focus mode.
  const { regenerate, stopAnswer, togglePause } = s;
  useEffect(() => {
    if (!live) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
      const key = e.key.toLowerCase();
      if (key === "r") regenerate();
      else if (key === "s") stopAnswer();
      else if (key === "p") togglePause();
      else if (key === "f") setFocus((f) => !f);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [live, regenerate, stopAnswer, togglePause]);

  const limitHit = s.notice?.code === "limit" || (usage?.remaining_seconds === 0);

  return (
    <div className="flex h-screen flex-col">
      <TopBar right={<LiveStatus status={s.status} paused={s.paused} remaining={s.remaining} />} />

      <main className="mx-auto flex w-full max-w-6xl min-h-0 flex-1 flex-col gap-4 px-4 py-5 sm:px-6">
        {idle ? (
          <>
            <SessionSetup onChange={onSetupChange} />
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
              <Button size="lg" onClick={() => s.start(setupRef.current)} disabled={limitHit || !hasCv}>
                Start interview
              </Button>
              <p className="text-sm text-muted">
                {hasCv
                  ? <>Pick your meeting tab when asked and turn on &ldquo;Share tab audio&rdquo;.</>
                  : "Upload your CV to start. Answers are written only from it."}
              </p>
              {usage && (
                <p className="text-sm text-muted sm:ml-auto">
                  {usage.plan.name} plan: {minutesLeft(usage.remaining_seconds)}
                </p>
              )}
            </div>
          </>
        ) : (
          // Long CV/JD titles are shortened with "..." so the controls always stay on one line together.
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
            <p
              className="min-w-0 flex-1 truncate text-sm text-muted"
              title={s.using ? `${s.using.cv}${s.using.jd ? ` for ${s.using.jd}` : ""}` : undefined}
            >
              {s.using ? (
                <>
                  Answering from <span className="font-semibold text-text">{s.using.cv}</span>
                  {s.using.jd && <> for <span className="font-semibold text-text">{s.using.jd}</span></>}
                </>
              ) : (
                s.status === "connecting" && "Connecting to the transcription service. On a slow connection this can take up to 30 seconds."
              )}
            </p>
            <div className="flex flex-none flex-wrap gap-2">
              {inInterview && (
                <>
                  <Button variant="secondary" size="sm" onClick={() => setFocus((f) => !f)} aria-pressed={focus}>
                    {focus ? "Show transcript" : "Focus mode"}
                  </Button>
                  <Button variant="secondary" size="sm" onClick={s.togglePause}>
                    {s.paused ? "Resume listening" : "Pause listening"}
                  </Button>
                </>
              )}
              <Button variant="secondary" size="sm" onClick={s.end}>End interview</Button>
            </div>
          </div>
        )}

        {s.notice && (
          <Notice tone={s.notice.kind} className="flex items-start justify-between gap-4">
            <span>
              {s.notice.text}
              {s.notice.code === "limit" && (
                <> <Link href="/#pricing" className="font-semibold underline underline-offset-4">See plans</Link></>
              )}
            </span>
            <button aria-label="Dismiss" className="text-muted hover:text-text" onClick={() => s.setNotice(null)}>×</button>
          </Notice>
        )}

        <div className={cn("grid min-h-0 flex-1 gap-4", focus && inInterview ? "grid-cols-1" : "md:grid-cols-[2fr_3fr]")}>
          {!(focus && inInterview) && (
            <TranscriptPanel
              lines={s.lines}
              partial={s.partial}
              emptyText={inInterview ? "Listening..." : "The interviewer's words and yours appear here once you start."}
            />
          )}

          <section aria-label="Suggested answer" className="flex min-h-0 flex-col gap-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-muted">Answer</h2>
              {current && live && (
                current.streaming ? (
                  <Button variant="ghost" size="sm" onClick={s.stopAnswer}>Stop <Kbd>S</Kbd></Button>
                ) : (
                  <Button variant="ghost" size="sm" onClick={s.regenerate}>Regenerate <Kbd>R</Kbd></Button>
                )
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {current ? (
                <>
                  <CueCard qa={current} size={focus ? "xl" : "lg"} />
                  {previous.length > 0 && (
                    <div className="mt-6">
                      <h3 className="mb-2 text-sm font-semibold text-muted">Earlier questions</h3>
                      <ul className="divide-y divide-line border-y border-line">
                        {previous.map((qa) => (
                          <li key={qa.id}>
                            <details className="group py-2">
                              <summary className="cursor-pointer text-sm text-muted group-open:text-text">{qa.question}</summary>
                              <div className="mt-2"><CueCard qa={qa} size="sm" /></div>
                            </details>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              ) : (
                <div className="flex h-full min-h-48 items-center justify-center rounded-card border border-dashed border-line p-6 text-center text-muted">
                  {inInterview ? "Waiting for the interviewer's first question." : "Your answer appears here, on a card like this, as soon as a question is asked."}
                </div>
              )}
            </div>
            {live && (
              <p className="hidden text-xs text-muted md:block">
                Shortcuts: <Kbd>R</Kbd> regenerate <Kbd>S</Kbd> stop answer <Kbd>P</Kbd> pause <Kbd>F</Kbd> focus mode
              </p>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
