"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageShell } from "@/components/AppShell";
import { Button, LinkButton } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Field";
import { Badge, formatDuration, Meter } from "@/components/ui/Misc";
import { DocKind, DocSummary, InterviewSummary, Plan, Usage, useApi } from "@/lib/api";

const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "long" });
const TYPE: Record<string, string> = { behavioral: "Behavioral", technical: "Technical", mixed: "Mixed" };

const STATUS_NOTE: Record<string, string> = {
  past_due: "Your last payment didn't go through. Update your card to keep your plan.",
  unpaid: "Your subscription is unpaid, so you're on the Free plan. Update your card to restore it.",
};

function PlanSection({ usage, plans }: { usage: Usage | null; plans: Plan[] }) {
  const { api, onError } = useApi();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!usage) return <div className="h-28 animate-pulse rounded-panel border border-line" />;
  const used = Math.floor(usage.used_seconds / 60);
  const limit = usage.limit_seconds == null ? null : usage.limit_seconds / 60;
  const { billing } = usage;
  const upgrades = usage.plan.id === "free" ? plans.filter((p) => p.purchasable) : [];
  const note = billing.subscription_status ? STATUS_NOTE[billing.subscription_status] : undefined;

  // Stripe hosts both pages; the browser leaves the app and comes back to the dashboard.
  const go = async (label: string, fn: () => Promise<{ url: string }>) => {
    setBusy(label);
    setError(null);
    try {
      window.location.assign((await fn()).url);
    } catch (e) {
      setError(onError(e));
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby="plan" className="rounded-panel border border-line p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="plan" className="text-lg font-bold">{usage.plan.name} plan</h2>
        <Link href="/#pricing" className="text-sm text-muted underline underline-offset-4 hover:text-text">
          Compare plans
        </Link>
      </div>
      {limit == null ? (
        <p className="mt-2 text-muted">Unlimited interview minutes. {used} min used this month.</p>
      ) : (
        <div className="mt-3 flex flex-col gap-2">
          <Meter value={used} max={limit} label="Interview minutes used this month" />
          <p className="text-sm text-muted">
            <span className="font-semibold text-text">{used} of {limit} minutes</span> used this month. Resets on{" "}
            {dayFmt.format(new Date(usage.resets_at))}.
          </p>
        </div>
      )}
      {note && <Notice tone="warning" className="mt-4">{note}</Notice>}
      {(upgrades.length > 0 || billing.can_manage) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {upgrades.map((p, i) => (
            <Button
              key={p.id}
              size="sm"
              variant={i === 0 ? "primary" : "secondary"}
              disabled={!!busy}
              onClick={() => go(p.id, () => api.checkout(p.id))}
            >
              {busy === p.id ? "Opening checkout..." : `Upgrade to ${p.name}, ${p.price_label}`}
            </Button>
          ))}
          {billing.can_manage && (
            <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => go("portal", api.portal)}>
              {busy === "portal" ? "Opening..." : "Manage subscription"}
            </Button>
          )}
        </div>
      )}
      {billing.can_manage && (
        <p className="mt-2 text-xs text-muted">Change plan, update your card, see invoices or cancel.</p>
      )}
      {usage.plan.id === "free" && !billing.payments_enabled && (
        <p className="mt-4 text-sm text-muted">Paid plans aren&rsquo;t available to buy yet.</p>
      )}
      {error && <Notice className="mt-4">{error}</Notice>}
    </section>
  );
}

function DocumentsSection({ docs, onChanged }: { docs: DocSummary[]; onChanged: () => void }) {
  const { api, onError } = useApi();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputs = { cv: useRef<HTMLInputElement>(null), jd: useRef<HTMLInputElement>(null) };

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(onError(e));
    } finally {
      setBusy(null);
    }
  };

  const upload = (kind: DocKind) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) void run(`Reading ${file.name}...`, () => api.uploadDocument(kind, file));
  };

  const group = (kind: DocKind, title: string, empty: string) => {
    const items = docs.filter((d) => d.kind === kind);
    return (
      <div>
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">{title}</h3>
          <Button variant="secondary" size="sm" disabled={!!busy} onClick={() => inputs[kind].current?.click()}>
            Upload
          </Button>
          <input ref={inputs[kind]} type="file" accept=".pdf,.docx,.txt" hidden onChange={upload(kind)} />
        </div>
        {items.length === 0 ? (
          <p className="mt-2 text-sm text-muted">{empty}</p>
        ) : (
          <ul className="mt-2 divide-y divide-line border-y border-line">
            {items.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="truncate font-semibold">{d.title}</p>
                  <p className="text-xs text-muted">
                    Added {dateFmt.format(new Date(d.created_at))}, {d.chars.toLocaleString()} characters
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!!busy}
                  onClick={() => run("Deleting...", () => api.deleteDocument(d.id))}
                  aria-label={`Delete ${d.title}`}
                >
                  Delete
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  };

  return (
    <section aria-labelledby="docs" className="flex flex-col gap-6">
      <h2 id="docs" className="text-lg font-bold">Your documents</h2>
      {group("cv", "CVs", "No CV yet. Upload a PDF or Word file so answers use your experience.")}
      {group("jd", "Job descriptions", "Optional. Add one to tailor answers to the role. You can also paste one on the copilot page.")}
      {busy && <p className="text-sm text-muted" role="status">{busy}</p>}
      {error && <Notice>{error}</Notice>}
    </section>
  );
}

function InterviewsSection({ items }: { items: InterviewSummary[] | null }) {
  return (
    <section aria-labelledby="interviews" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 id="interviews" className="text-lg font-bold">Recent interviews</h2>
        <LinkButton href="/copilot" size="sm">Start interview</LinkButton>
      </div>
      {items == null ? (
        <div className="h-24 animate-pulse rounded-panel border border-line" />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted">
          No interviews yet. When you run the copilot, the transcript and every answer are saved here.
        </p>
      ) : (
        <ul className="divide-y divide-line border-y border-line">
          {items.map((it) => (
            <li key={it.id}>
              <Link href={`/dashboard/interviews/${it.id}`} className="flex items-center gap-3 py-3 hover:bg-panel/60">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{dateFmt.format(new Date(it.started_at))}</p>
                  <p className="truncate text-sm text-muted">
                    {it.cv_title}
                    {it.jd_title ? ` for ${it.jd_title}` : ""}
                  </p>
                </div>
                <div className="flex flex-none flex-col items-end gap-1 text-sm text-muted">
                  <Badge>{TYPE[it.session_type] ?? it.session_type}</Badge>
                  <span>
                    {it.questions} {it.questions === 1 ? "question" : "questions"}, {formatDuration(it.seconds_used)}
                  </span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** What brought the user here: back from Stripe Checkout, or a plan chosen on the landing page. */
type Arrival = { kind: "success" } | { kind: "cancelled" } | { kind: "upgrade"; plan: string } | null;

function readArrival(): Arrival {
  const q = new URLSearchParams(window.location.search);
  if (q.get("checkout") === "success") return { kind: "success" };
  if (q.get("checkout") === "cancelled") return { kind: "cancelled" };
  const plan = q.get("upgrade");
  return plan === "pro" || plan === "premium" ? { kind: "upgrade", plan } : null;
}

const POLL_MS = 2000;
const POLL_TRIES = 15;

export default function DashboardPage() {
  const { api, token, onError } = useApi();
  const [usage, setUsage] = useState<Usage | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [docs, setDocs] = useState<DocSummary[]>([]);
  const [interviews, setInterviews] = useState<InterviewSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "info" | "warning"; text: string } | null>(null);
  const arrival = useRef<Arrival | undefined>(undefined);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [u, d, i, p] = await Promise.all([api.usage(), api.listDocuments(), api.interviews(), api.plans()]);
      setUsage(u);
      setDocs(d);
      setInterviews(i);
      setPlans(p);
    } catch (e) {
      setError(onError(e));
    }
  }, [api, token, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  // Handle ?checkout= and ?upgrade= once, then clean the address bar.
  useEffect(() => {
    if (!token || arrival.current !== undefined) return;
    const a = readArrival();
    arrival.current = a;
    if (!a) return;
    window.history.replaceState(null, "", "/dashboard");
    if (a.kind === "cancelled") {
      setNotice({ tone: "info", text: "Checkout cancelled. Nothing was charged." });
    } else if (a.kind === "upgrade") {
      setNotice({ tone: "info", text: "Opening checkout..." });
      api.checkout(a.plan).then(
        ({ url }) => window.location.assign(url),
        (e) => {
          setNotice(null);
          setError(onError(e));
        },
      );
    } else {
      // Stripe confirms the payment to the backend separately (webhook), usually within seconds.
      setNotice({ tone: "info", text: "Payment received. Updating your plan..." });
      let tries = 0;
      const timer = window.setInterval(async () => {
        tries += 1;
        try {
          const u = await api.usage();
          if (u.plan.id !== "free") {
            window.clearInterval(timer);
            setUsage(u);
            setNotice({ tone: "info", text: `You're on the ${u.plan.name} plan.` });
            return;
          }
        } catch {
          /* keep trying */
        }
        if (tries >= POLL_TRIES) {
          window.clearInterval(timer);
          setNotice({
            tone: "warning",
            text: "Your payment went through, but the plan hasn't updated yet. Refresh this page in a minute.",
          });
        }
      }, POLL_MS);
      return () => window.clearInterval(timer);
    }
  }, [api, token, onError]);

  return (
    <PageShell wide>
      <h1 className="text-2xl font-bold">Dashboard</h1>
      {notice && <Notice tone={notice.tone} className="mt-4">{notice.text}</Notice>}
      {error && <Notice className="mt-4">{error}</Notice>}
      <div className="mt-6 grid gap-10 lg:grid-cols-[1fr_1.2fr]">
        <div className="flex flex-col gap-10">
          <PlanSection usage={usage} plans={plans} />
          <DocumentsSection docs={docs} onChanged={load} />
        </div>
        <InterviewsSection items={interviews} />
      </div>
    </PageShell>
  );
}
