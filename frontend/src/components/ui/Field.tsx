import { forwardRef } from "react";
import { cn } from "./cn";

const control =
  "w-full rounded-control border border-line bg-panel px-3 text-text placeholder:text-muted " +
  "focus:border-accent focus:outline-none";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(control, "h-10", className)} {...props} />;
});

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, ...props },
  ref,
) {
  return <select ref={ref} className={cn(control, "h-10 pr-8", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={cn(control, "min-h-32 py-2", className)} {...props} />;
  },
);

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("flex flex-col gap-1.5 text-sm font-semibold", className)} {...props} />;
}

export function Hint({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("text-xs font-normal text-muted", className)} {...props} />;
}

export function Notice({
  tone = "error",
  className,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement> & { tone?: "error" | "warning" | "info" }) {
  const tones = {
    error: "border-live/60 bg-live/10 text-text",
    warning: "border-warn/60 bg-warn/10 text-text",
    info: "border-line bg-panel text-muted",
  };
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={cn("rounded-panel border px-4 py-2.5 text-sm", tones[tone], className)}
      {...props}
    />
  );
}
