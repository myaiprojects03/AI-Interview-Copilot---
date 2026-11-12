"use client";

import Link from "next/link";
import { signOut } from "next-auth/react";
import { useEffect, useState } from "react";
import { PageShell } from "@/components/AppShell";
import { Button } from "@/components/ui/Button";
import { Input, Label, Notice } from "@/components/ui/Field";
import { Me, useApi } from "@/lib/api";

export default function AccountPage() {
  const { api, token, onError } = useApi();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (token) api.me().then(setMe).catch((e) => setError(onError(e)));
  }, [api, token, onError]);

  const deleteAccount = async () => {
    setDeleting(true);
    setError(null);
    try {
      await api.deleteAccount();
      await signOut({ callbackUrl: "/login" });
    } catch (e) {
      setError(onError(e));
      setDeleting(false);
    }
  };

  const methods = me ? [me.has_password && "email and password", me.google && "Google"].filter(Boolean).join(" and ") : "";

  return (
    <PageShell>
      <h1 className="text-2xl font-bold">Account</h1>

      <section aria-labelledby="profile" className="mt-6 rounded-panel border border-line p-5">
        <h2 id="profile" className="sr-only">Profile</h2>
        <dl className="grid gap-4 sm:grid-cols-[10rem_1fr]">
          <dt className="text-muted">Name</dt>
          <dd>{me ? me.name || "Not set" : "..."}</dd>
          <dt className="text-muted">Email</dt>
          <dd>{me?.email ?? "..."}</dd>
          <dt className="text-muted">Sign-in</dt>
          <dd>{methods ? `With ${methods}` : "..."}</dd>
        </dl>
        <Button variant="secondary" size="sm" className="mt-5" onClick={() => signOut({ callbackUrl: "/login" })}>
          Sign out
        </Button>
      </section>

      {me?.is_admin && (
        <section aria-labelledby="admin" className="mt-6 rounded-panel border border-line p-5">
          <h2 id="admin" className="text-lg font-bold">Administration</h2>
          <p className="mt-1 text-muted">
            <Link href="/admin/latency" className="font-semibold text-text underline underline-offset-4">
              Answer speed
            </Link>{" "}
            shows how long answers take across all users.
          </p>
        </section>
      )}

      <section aria-labelledby="delete" className="mt-10 rounded-panel border border-live/50 p-5">
        <h2 id="delete" className="text-lg font-bold">Delete account</h2>
        <p className="mt-1 max-w-prose text-muted">
          Permanently deletes your account, your CVs and job descriptions, their search data and your interview
          history. A paid subscription is cancelled at the same time, with no further charges. This cannot be
          undone.
        </p>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <Label className="w-full max-w-xs">
            Type DELETE to confirm
            <Input value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-label="Type DELETE to confirm" />
          </Label>
          <Button variant="danger" disabled={confirm !== "DELETE" || deleting} onClick={deleteAccount}>
            {deleting ? "Deleting..." : "Delete my account"}
          </Button>
        </div>
        {error && <Notice className="mt-4">{error}</Notice>}
      </section>
    </PageShell>
  );
}
