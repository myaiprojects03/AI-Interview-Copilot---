import type { Plan } from "@/lib/api";

/** Server-side: plans from the backend (the single source of plan names, prices and limits).
 *  Waits at most 4 s: on a free host the backend may be waking up (about a minute). The page then
 *  renders without the pricing section instead of hanging, and the request still wakes the backend. */
export async function fetchPlans(): Promise<Plan[]> {
  try {
    const res = await fetch(`${process.env.BACKEND_URL ?? "http://localhost:8000"}/api/plans`, {
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(4000),
    });
    return res.ok ? ((await res.json()) as Plan[]) : [];
  } catch {
    return [];
  }
}
