"use client";

import { useEffect, useState } from "react";
import { PageShell } from "@/components/AppShell";
import { cn } from "@/components/ui/cn";
import { Notice } from "@/components/ui/Field";
import { LatencyReport, useApi } from "@/lib/api";

const PERIODS = [
  { days: 1, label: "Last 24 hours" },
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
];

// In the order the steps happen; the first row is the delay the person in the interview feels.
const ROWS: { key: keyof LatencyReport["metrics"]; label: string; hint: string }[] = [
  { key: "first_token_ms", label: "First words on screen", hint: "What the user waits for" },
  { key: "detect_ms", label: "Question recognised", hint: "Question detection model" },
  { key: "retrieval_ms", label: "CV search", hint: "Runs alongside detection" },
  { key: "total_ms", label: "Full answer written", hint: "Until the last word" },
];

function seconds(ms: number | null) {
  return ms == null ? "No data" : `${(ms / 1000).toFixed(1)} s`;
}

export default function LatencyPage() {
  const { api, token, onError } = useApi();
  const [days, setDays] = useState(7);
  const [report, setReport] = useState<LatencyReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    setReport(null);
    setError(null);
    api.latency(days).then(setReport).catch((e) => setError(onError(e)));
  }, [api, token, days, onError]);

  return (
    <PageShell>
      <h1 className="text-2xl font-bold">Answer speed</h1>
      <p className="mt-1 max-w-prose text-muted">
        How long answers take, across all users. Timed on the server from the moment the interviewer finishes
        speaking. No interview content is shown here.
      </p>

      <div role="group" aria-label="Period" className="mt-6 flex flex-wrap gap-2">
        {PERIODS.map((p) => (
          <button
            key={p.days}
            onClick={() => setDays(p.days)}
            aria-pressed={days === p.days}
            className={cn(
              "rounded-control border px-3 py-1.5 text-sm",
              days === p.days ? "border-text bg-panel font-semibold" : "border-line text-muted hover:text-text",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      {error && <Notice className="mt-6">{error}</Notice>}
      {!error && !report && <div className="mt-6 h-64 animate-pulse rounded-panel border border-line" />}
      {report && (
        <section aria-label="Latency" className="mt-6">
          {report.answers === 0 ? (
            <p className="text-muted">No answers in this period yet.</p>
          ) : (
            <>
              <p>
                <span className="font-semibold">{report.answers.toLocaleString()} answers.</span>{" "}
                {report.first_words_under_2s != null && (
                  <>
                    {Math.round(report.first_words_under_2s * 100)}% showed their first words within 2 seconds.
                  </>
                )}
              </p>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[32rem] text-left text-sm">
                  <thead className="text-muted">
                    <tr className="border-b border-line">
                      <th scope="col" className="py-2 pr-4 font-semibold">Step</th>
                      <th scope="col" className="py-2 pr-4 text-right font-semibold">Typical</th>
                      <th scope="col" className="py-2 pr-4 text-right font-semibold">Slow (1 in 20)</th>
                      <th scope="col" className="py-2 text-right font-semibold">Slowest</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {ROWS.map((row, i) => {
                      const m = report.metrics[row.key];
                      return (
                        <tr key={row.key} className={i === 0 ? "text-base" : undefined}>
                          <th scope="row" className="py-3 pr-4 font-normal">
                            <span className={i === 0 ? "font-semibold" : undefined}>{row.label}</span>
                            <span className="block text-xs text-muted">{row.hint}</span>
                          </th>
                          <td className="py-3 pr-4 text-right tabular-nums">{seconds(m.p50)}</td>
                          <td className="py-3 pr-4 text-right tabular-nums">{seconds(m.p95)}</td>
                          <td className="py-3 text-right tabular-nums">{seconds(m.max)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-4 text-xs text-muted">
                Typical is the median; slow is the 95th percentile. Every answer is also written to the server log
                as one &ldquo;latency&rdquo; line.
              </p>
            </>
          )}
        </section>
      )}
    </PageShell>
  );
}
