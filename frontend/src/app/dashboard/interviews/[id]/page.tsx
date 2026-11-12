"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PageShell } from "@/components/AppShell";
import { CueCard } from "@/components/copilot/CueCard";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Field";
import { Badge, formatDuration } from "@/components/ui/Misc";
import { InterviewDetail, useApi } from "@/lib/api";

const dateFmt = new Intl.DateTimeFormat(undefined, { dateStyle: "full", timeStyle: "short" });
const TYPE: Record<string, string> = { behavioral: "Behavioral", technical: "Technical", mixed: "Mixed" };

export default function InterviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { api, token, onError } = useApi();
  const [data, setData] = useState<InterviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (token) api.interview(id).then(setData).catch((e) => setError(onError(e)));
  }, [api, token, id, onError]);

  const remove = async () => {
    try {
      await api.deleteInterview(id);
      router.replace("/dashboard");
    } catch (e) {
      setError(onError(e));
    }
  };

  return (
    <PageShell>
      <Link href="/dashboard" className="text-sm text-muted underline underline-offset-4 hover:text-text">
        Back to dashboard
      </Link>
      {error && <Notice className="mt-4">{error}</Notice>}
      {data && (
        <>
          <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold">Interview on {dateFmt.format(new Date(data.started_at))}</h1>
              <p className="mt-1 text-muted">
                {data.cv_title}
                {data.jd_title ? ` for ${data.jd_title}` : ""}, {formatDuration(data.seconds_used)}
              </p>
            </div>
            <Badge>{TYPE[data.session_type] ?? data.session_type}</Badge>
          </header>

          <section aria-labelledby="answers" className="mt-8">
            <h2 id="answers" className="text-lg font-bold">
              Questions and answers <span className="font-normal text-muted">({data.answers.length})</span>
            </h2>
            {data.answers.length === 0 ? (
              <p className="mt-2 text-muted">No questions were detected in this interview.</p>
            ) : (
              <div className="mt-4 flex flex-col gap-4">
                {data.answers.map((a) => (
                  <CueCard
                    key={a.question_id}
                    size="sm"
                    qa={{ id: a.question_id, question: a.question, category: a.category, answer: a.answer,
                          streaming: false, stopped: false, unverified: a.unverified,
                          timings: { detect_ms: null, first_token_ms: a.timings.first_token_ms ?? null, total_ms: null } }}
                  />
                ))}
              </div>
            )}
          </section>

          <section aria-labelledby="transcript" className="mt-10">
            <details>
              <summary className="cursor-pointer text-lg font-bold" id="transcript">
                Full transcript <span className="font-normal text-muted">({data.lines.length} lines)</span>
              </summary>
              <div className="mt-4 space-y-3 text-sm leading-relaxed">
                {data.lines.map((l) => (
                  <p key={l.seq}>
                    <span className={l.speaker === "interviewer" ? "mr-2 font-semibold text-accent" : "mr-2 font-semibold text-muted"}>
                      {l.speaker === "interviewer" ? "Interviewer" : "You"}
                    </span>
                    {l.text}
                  </p>
                ))}
              </div>
            </details>
          </section>

          <section className="mt-12 border-t border-line pt-6">
            {confirming ? (
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-sm">Delete this interview and its transcript? This cannot be undone.</p>
                <Button variant="danger" size="sm" onClick={remove}>Delete interview</Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>Cancel</Button>
              </div>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>Delete this interview</Button>
            )}
          </section>
        </>
      )}
    </PageShell>
  );
}
