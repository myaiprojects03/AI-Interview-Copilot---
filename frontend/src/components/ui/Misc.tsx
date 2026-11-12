import { cn } from "./cn";

export function Badge({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn("inline-flex items-center rounded-full border border-line px-2.5 py-0.5 text-xs font-semibold", className)}
      {...props}
    />
  );
}

/** A labelled progress bar (used for plan minutes). */
export function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div className="flex flex-col gap-2">
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        className="h-2 w-full overflow-hidden rounded-full bg-line"
      >
        <div className={cn("h-full rounded-full", pct >= 90 ? "bg-live" : "bg-accent")} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

/** Logo mark: a cue card with one highlighted line. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={cn("h-6 w-6", className)}>
      <rect x="2.5" y="4.5" width="19" height="15" rx="3" fill="var(--c-paper)" />
      <rect x="5.5" y="9" width="13" height="3.2" rx="1" fill="var(--c-accent)" />
      <rect x="5.5" y="14.2" width="9" height="1.6" rx="0.8" fill="var(--c-paperMuted)" />
    </svg>
  );
}

export function formatMinutes(seconds: number) {
  const m = Math.floor(seconds / 60);
  return `${m} min`;
}

export function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds} s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m} min ${s} s` : `${m} min`;
}
