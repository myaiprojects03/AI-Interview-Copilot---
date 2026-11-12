import { cva, type VariantProps } from "class-variance-authority";
import Link from "next/link";
import { forwardRef } from "react";
import { cn } from "./cn";

const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-control font-semibold transition-colors " +
    "disabled:pointer-events-none disabled:opacity-50 whitespace-nowrap",
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-ink hover:brightness-105",
        secondary: "border border-line bg-panel text-text hover:border-muted",
        ghost: "text-text hover:bg-panel",
        danger: "bg-live text-white hover:brightness-110",
      },
      size: {
        sm: "h-8 px-3 text-sm",
        md: "h-10 px-4 text-sm",
        lg: "h-12 px-6 text-base",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof button>;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, type = "button", ...props },
  ref,
) {
  return <button ref={ref} type={type} className={cn(button({ variant, size }), className)} {...props} />;
});

type LinkButtonProps = React.ComponentProps<typeof Link> & VariantProps<typeof button>;

export function LinkButton({ className, variant, size, ...props }: LinkButtonProps) {
  return <Link className={cn(button({ variant, size }), className)} {...props} />;
}
