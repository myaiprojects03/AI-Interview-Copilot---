import Link from "next/link";
import { Wordmark } from "@/components/AppShell";
import { brand } from "@/config/brand";

/** Public text page (Privacy, Terms): readable column, no sign-in needed. */
export function LegalPage({ title, updated, children }: { title: string; updated: string; children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="border-b border-line">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-4 sm:px-6">
          <Wordmark />
          <Link href="/" className="text-sm text-muted hover:text-text">Back to home</Link>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <h1 className="text-3xl font-bold">{title}</h1>
        <p className="mt-2 text-sm text-muted">Last updated {updated}</p>
        <div className="legal mt-8 flex max-w-prose flex-col gap-4 leading-relaxed">{children}</div>
        <p className="mt-10 text-sm text-muted">
          {brand.contactEmail ? (
            <>Questions: <a className="underline underline-offset-4" href={`mailto:${brand.contactEmail}`}>{brand.contactEmail}</a></>
          ) : (
            <>See also the <Link className="underline underline-offset-4" href={title === "Privacy" ? "/terms" : "/privacy"}>
              {title === "Privacy" ? "Terms" : "Privacy"}</Link> page.</>
          )}
        </p>
      </main>
    </div>
  );
}
