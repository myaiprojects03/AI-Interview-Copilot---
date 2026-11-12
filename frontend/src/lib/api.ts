"use client";

import { signOut, useSession } from "next-auth/react";
import { useCallback, useMemo } from "react";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const WS_URL = `${API_URL.replace(/^http/, "ws")}/ws/session`;

export type DocKind = "cv" | "jd";
export type DocSummary = {
  id: string;
  kind: DocKind;
  title: string;
  chars: number;
  preview: string;
  created_at: string;
};
export type Me = {
  id: string;
  email: string;
  name: string;
  plan: string;
  has_password: boolean;
  google: boolean;
  is_admin: boolean;
};
export type LatencyMetric = { p50: number | null; p95: number | null; max: number | null };
export type LatencyReport = {
  days: number;
  since: string;
  answers: number;
  metrics: Record<"detect_ms" | "retrieval_ms" | "first_token_ms" | "total_ms", LatencyMetric>;
  first_words_under_2s: number | null;
};
export type Plan = {
  id: string;
  name: string;
  price_label: string;
  monthly_minutes: number | null;
  features: string[];
  /** Payments are set up and this plan has a Stripe price. */
  purchasable: boolean;
};
export type Billing = {
  payments_enabled: boolean;
  /** Stripe subscription status: active, trialing, past_due, canceled... null if never subscribed. */
  subscription_status: string | null;
  /** Has a Stripe customer, so the self-service portal can be opened. */
  can_manage: boolean;
};
export type Usage = {
  plan: Plan;
  billing: Billing;
  used_seconds: number;
  limit_seconds: number | null;
  remaining_seconds: number | null;
  resets_at: string;
};
export type InterviewSummary = {
  id: string;
  started_at: string;
  ended_at: string | null;
  seconds_used: number;
  session_type: string;
  cv_title: string;
  jd_title: string | null;
  questions: number;
};
export type InterviewAnswer = {
  question_id: string;
  question: string;
  category: string;
  answer: string;
  unverified: string[];
  timings: { first_token_ms?: number | null };
  created_at: string;
};
export type InterviewDetail = InterviewSummary & {
  lines: { seq: number; speaker: "interviewer" | "candidate"; text: string }[];
  answers: InterviewAnswer[];
};

/** The session is missing or expired: the user must sign in again. */
export class AuthError extends Error {}

/** The request never reached the server (offline, server down). */
export class NetworkError extends Error {
  constructor() {
    super(
      process.env.NODE_ENV === "development"
        ? "Can't reach the backend. Is it running on port 8000?"
        : "Can't reach the server. Check your internet connection and try again.",
    );
  }
}

async function request<T>(token: string | undefined, path: string, init: RequestInit = {}): Promise<T> {
  if (!token) throw new AuthError("Please sign in.");
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new NetworkError();
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail[0]?.msg ?? detail;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401) throw new AuthError(detail);
    throw new Error(detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export function createApi(token: string | undefined) {
  return {
    me: () => request<Me>(token, "/api/me"),
    deleteAccount: () => request<void>(token, "/api/me", { method: "DELETE" }),
    listDocuments: () => request<DocSummary[]>(token, "/api/documents"),
    uploadDocument(kind: DocKind, file: File) {
      const form = new FormData();
      form.append("kind", kind);
      form.append("file", file);
      return request<DocSummary>(token, "/api/documents/upload", { method: "POST", body: form });
    },
    saveText: (kind: DocKind, text: string, title?: string) =>
      request<DocSummary>(token, "/api/documents/text", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, text, title }),
      }),
    deleteDocument: (id: string) => request<void>(token, `/api/documents/${id}`, { method: "DELETE" }),
    usage: () => request<Usage>(token, "/api/usage"),
    interviews: () => request<InterviewSummary[]>(token, "/api/sessions"),
    interview: (id: string) => request<InterviewDetail>(token, `/api/sessions/${id}`),
    deleteInterview: (id: string) => request<void>(token, `/api/sessions/${id}`, { method: "DELETE" }),
    plans: () => request<Plan[]>(token, "/api/plans"),
    /** Stripe Checkout (or the portal, if already subscribed): the page to send the browser to. */
    checkout: (plan: string) =>
      request<{ url: string }>(token, "/api/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan }),
      }),
    portal: () => request<{ url: string }>(token, "/api/billing/portal", { method: "POST" }),
    /** A single-use ticket for opening the live-session WebSocket (valid 30 s). */
    sessionTicket: () => request<{ ticket: string; expires_in: number }>(token, "/api/session/ticket", { method: "POST" }),
    latency: (days: number) => request<LatencyReport>(token, `/api/admin/latency?days=${days}`),
  };
}

export type Api = ReturnType<typeof createApi>;

/** API bound to the signed-in user's token, plus a handler that signs out on expired sessions. */
export function useApi() {
  const { data: session, status } = useSession();
  const token = session && !session.expired ? session.accessToken : undefined;
  const api = useMemo(() => createApi(token), [token]);
  const onError = useCallback((e: unknown): string => {
    if (e instanceof AuthError) {
      void signOut({ callbackUrl: "/login?expired=1" });
      return "Your session has expired. Signing you out...";
    }
    return e instanceof Error ? e.message : String(e);
  }, []);
  return { api, token, ready: status !== "loading", onError };
}
