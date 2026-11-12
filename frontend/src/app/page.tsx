import { getServerSession } from "next-auth";
import { Wordmark } from "@/components/AppShell";
import { Button, LinkButton } from "@/components/ui/Button";
import { cn } from "@/components/ui/cn";
import { brand } from "@/config/brand";
import { authOptions } from "@/lib/authOptions";
import type { Plan } from "@/lib/api";
import { fetchPlans } from "@/lib/plans";

const STEPS = [
  { title: "Add your CV", body: "Upload a PDF or Word file. Add the job description too, if you have it." },
  { title: "Share your interview tab", body: "Join the call in Chrome or Edge, then share that tab's audio with the copilot." },
  {
    title: "Glance at the cue card",
    body: "Each question gets an answer from your own experience: a STAR story when they ask for an example, short points for everything else.",
  },
];

const TRUST = [
  { title: "Only your facts", body: "Answers are built from what your CV says. Nothing about you is made up." },
  {
    title: "Numbers are checked",
    body: "If an answer contains a figure that isn't in your CV, you see a warning before you say it.",
  },
  {
    title: "No invented failures",
    body: "Questions about mistakes, weaknesses or salary get a framework to fill in, never a fake story.",
  },
];

/** Free: the app. Paid: checkout, via sign-up first when needed (the dashboard starts the checkout). */
function planHref(plan: Plan, signedIn: boolean) {
  if (plan.id === "free") return signedIn ? "/dashboard" : "/login?mode=register";
  const target = `/dashboard?upgrade=${plan.id}`;
  return signedIn ? target : `/login?mode=register&callbackUrl=${encodeURIComponent(target)}`;
}

function DemoCueCard() {
  // A static example of the live screen. The answer writes itself in once, on load.
  const lines = [
    { label: "Situation", text: "At Northwind Energy the plant director planned a $2M equipment purchase." },
    { label: "Task", text: "Show whether the purchase was needed before the budget was committed." },
    { label: "Action", text: "Ran a six-month utilisation analysis: existing machines were at 55% capacity." },
    { label: "Result", text: "The purchase was postponed and the budget moved to maintenance." },
  ];
  return (
    <figure aria-label="Example of the copilot during an interview" className="relative">
      <div className="rounded-panel border border-line bg-panel p-4 text-sm">
        <p className="text-muted">
          <span className="font-semibold text-text">Interviewer</span>
          <span className="ml-2">Could you tell me about a time you convinced someone to change their mind?</span>
        </p>
      </div>
      <div className="-mt-1 ml-4 rounded-card bg-paper p-5 text-paper-ink shadow-[0_18px_40px_-18px_rgba(0,0,0,0.6)] sm:ml-10 sm:p-6">
        <div className="mb-3 flex items-center gap-2 text-xs text-paper-muted">
          <span className="rounded-full bg-paper-ink px-2 py-0.5 font-semibold text-paper">Behavioral</span>
          <span>Example</span>
        </div>
        <p className="write-in text-lg font-semibold leading-snug">
          <span className="highlight">I convinced the plant director to postpone a $2M purchase.</span>
        </p>
        <ul className="mt-3 flex flex-col gap-1.5 text-[0.95rem] leading-relaxed">
          {lines.map((l, i) => (
            <li key={l.label} className="write-in" style={{ animationDelay: `${300 + i * 260}ms` }}>
              <strong>{l.label}:</strong> {l.text}
            </li>
          ))}
        </ul>
      </div>
    </figure>
  );
}

export default async function Home() {
  const [session, plans] = await Promise.all([getServerSession(authOptions), fetchPlans()]);
  const signedIn = Boolean(session && !session.expired);

  return (
    <div className="min-h-screen">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Wordmark />
        <nav className="flex items-center gap-2">
          {signedIn ? (
            <LinkButton href="/copilot" size="sm">Open copilot</LinkButton>
          ) : (
            <>
              <LinkButton href="/login" variant="ghost" size="sm">Sign in</LinkButton>
              <LinkButton href="/login?mode=register" size="sm">Create account</LinkButton>
            </>
          )}
        </nav>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 pb-20 pt-10 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:pt-16">
          <div>
            <h1 className="max-w-[16ch] text-3xl font-bold sm:text-4xl">{brand.tagline}</h1>
            <p className="mt-5 max-w-prose text-lg text-muted">{brand.description}</p>
            <div className="mt-8 flex flex-wrap gap-3">
              <LinkButton href={signedIn ? "/copilot" : "/login?mode=register"} size="lg">
                {signedIn ? "Open copilot" : "Create a free account"}
              </LinkButton>
              {!signedIn && (
                <LinkButton href="/login" variant="secondary" size="lg">Sign in</LinkButton>
              )}
            </div>
            <p className="mt-4 text-sm text-muted">
              Works with interviews in Chrome or Edge, including Google Meet, Zoom and Teams in the browser.
            </p>
          </div>
          <DemoCueCard />
        </section>

        <section aria-labelledby="how" className="border-t border-line">
          <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
            <h2 id="how" className="text-2xl font-bold">How it works</h2>
            <ol className="mt-8 grid gap-8 md:grid-cols-3">
              {STEPS.map((s, i) => (
                <li key={s.title} className="flex gap-4">
                  <span aria-hidden className="mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-full bg-accent font-bold text-accent-ink">
                    {i + 1}
                  </span>
                  <div>
                    <h3 className="font-bold">{s.title}</h3>
                    <p className="mt-1 text-muted">{s.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section aria-labelledby="trust" className="border-t border-line">
          <div className="mx-auto grid max-w-6xl gap-10 px-4 py-16 sm:px-6 lg:grid-cols-[1fr_2fr]">
            <div>
              <h2 id="trust" className="text-2xl font-bold">Answers you can say out loud</h2>
              <p className="mt-3 max-w-prose text-muted">
                In an interview, a made-up fact is worse than no answer. The copilot is built around that.
              </p>
            </div>
            <dl className="divide-y divide-line border-y border-line">
              {TRUST.map((t) => (
                <div key={t.title} className="grid gap-1 py-5 sm:grid-cols-[12rem_1fr] sm:gap-6">
                  <dt className="font-bold">{t.title}</dt>
                  <dd className="text-muted">{t.body}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {plans.length > 0 && (
          <section aria-labelledby="pricing-title" className="border-t border-line" id="pricing">
            <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
              <h2 id="pricing-title" className="text-2xl font-bold">Plans</h2>
              <p className="mt-2 text-muted">Start free. Upgrade when you have more interviews lined up.</p>
              <div className="mt-8 grid gap-4 md:grid-cols-3">
                {plans.map((p) => {
                  const featured = p.id === "pro";
                  return (
                    <div
                      key={p.id}
                      className={cn(
                        "flex flex-col rounded-panel border p-6",
                        featured ? "border-accent bg-panel" : "border-line",
                      )}
                    >
                      <h3 className="text-lg font-bold">{p.name}</h3>
                      <p className="mt-1 text-xl font-bold">{p.price_label}</p>
                      <ul className="mt-4 flex flex-1 flex-col gap-2 text-sm text-muted">
                        {p.features.map((f) => (
                          <li key={f} className="flex gap-2">
                            <span aria-hidden className="text-accent">✓</span>
                            {f}
                          </li>
                        ))}
                      </ul>
                      {p.id === "free" || p.purchasable ? (
                        <LinkButton
                          href={planHref(p, signedIn)}
                          variant={featured ? "primary" : "secondary"}
                          className="mt-6"
                        >
                          {p.id === "free" ? "Start free" : `Choose ${p.name}`}
                        </LinkButton>
                      ) : (
                        <Button variant="secondary" className="mt-6" disabled>
                          Not available yet
                        </Button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </section>
        )}
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-8 text-sm text-muted sm:px-6">
          <span>© {new Date().getFullYear()} {brand.appName}</span>
          <span>Answers come from your CV. Always check them before you speak.</span>
        </div>
      </footer>
    </div>
  );
}
