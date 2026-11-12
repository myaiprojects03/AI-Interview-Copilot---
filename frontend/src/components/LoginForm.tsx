"use client";

import Link from "next/link";
import { signIn } from "next-auth/react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Wordmark } from "@/components/AppShell";
import { Button } from "@/components/ui/Button";
import { Hint, Input, Label, Notice } from "@/components/ui/Field";

function Form({ googleEnabled }: { googleEnabled: boolean }) {
  const router = useRouter();
  const params = useSearchParams();
  // Only same-site paths: an absolute or protocol-relative URL here would let a crafted link
  // send people to another site after they sign in.
  const requested = params.get("callbackUrl") ?? "";
  const callbackUrl = requested.startsWith("/") && !requested.startsWith("//") && !requested.startsWith("/\\")
    ? requested
    : "/copilot";
  const [mode, setMode] = useState<"login" | "register">(params.get("mode") === "register" ? "register" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(
    params.get("expired") ? "Your session has expired. Sign in again to continue." : null,
  );
  const register = mode === "register";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await signIn("credentials", { email, password, name, mode, redirect: false });
    setBusy(false);
    if (res?.ok && !res.error) {
      router.replace(callbackUrl);
      router.refresh();
    } else {
      setError(res?.error && res.error !== "CredentialsSignin" ? res.error : "Sign-in failed. Try again.");
    }
  };

  return (
    <div className="flex min-h-screen flex-col">
      <header className="mx-auto flex h-16 w-full max-w-6xl items-center px-4 sm:px-6">
        <Wordmark />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-10 sm:pt-20">
        <div className="w-full max-w-sm">
          <h1 className="text-2xl font-bold">{register ? "Create your account" : "Sign in"}</h1>
          <p className="mt-1 text-muted">
            {register ? "Free plan includes 30 interview minutes a month." : "Welcome back."}
          </p>

          {googleEnabled && (
            <>
              <Button variant="secondary" size="lg" className="mt-6 w-full" onClick={() => signIn("google", { callbackUrl })}>
                Continue with Google
              </Button>
              <p className="my-4 text-center text-sm text-muted">or with email</p>
            </>
          )}

          <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
            {register && (
              <Label>
                Name
                <Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
              </Label>
            )}
            <Label>
              Email
              <Input type="email" required value={email} autoComplete="email" onChange={(e) => setEmail(e.target.value)} />
            </Label>
            <Label>
              Password
              <Input
                type="password"
                required
                minLength={register ? 8 : undefined}
                value={password}
                autoComplete={register ? "new-password" : "current-password"}
                onChange={(e) => setPassword(e.target.value)}
              />
              {register && <Hint>At least 8 characters.</Hint>}
            </Label>
            {error && <Notice>{error}</Notice>}
            <Button type="submit" size="lg" disabled={busy}>
              {busy ? "Please wait..." : register ? "Create account" : "Sign in"}
            </Button>
            {register && (
              <p className="text-xs text-muted">
                By creating an account you agree to the{" "}
                <Link href="/terms" className="underline underline-offset-4 hover:text-text">Terms</Link> and{" "}
                <Link href="/privacy" className="underline underline-offset-4 hover:text-text">Privacy</Link> pages.
              </p>
            )}
          </form>

          <p className="mt-6 text-sm text-muted">
            {register ? "Already have an account?" : "New here?"}{" "}
            <button
              type="button"
              className="font-semibold text-text underline underline-offset-4"
              onClick={() => {
                setMode(register ? "login" : "register");
                setError(null);
              }}
            >
              {register ? "Sign in" : "Create an account"}
            </button>
          </p>
        </div>
      </main>
    </div>
  );
}

export function LoginForm({ googleEnabled }: { googleEnabled: boolean }) {
  return (
    <Suspense>
      <Form googleEnabled={googleEnabled} />
    </Suspense>
  );
}
