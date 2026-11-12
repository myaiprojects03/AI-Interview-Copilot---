import type { Config } from "tailwindcss";

// Opacity-aware: `bg-live/10` mixes the token with transparent (Tailwind fills in <alpha-value>).
const token = (name: string) => `color-mix(in srgb, var(--c-${name}) calc(<alpha-value> * 100%), transparent)`;

export default {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: token("ink"),
        panel: token("panel"),
        line: token("line"),
        text: token("text"),
        muted: token("muted"),
        paper: token("paper"),
        "paper-ink": token("paperInk"),
        "paper-muted": token("paperMuted"),
        accent: token("accent"),
        "accent-ink": token("accentInk"),
        live: token("live"),
        warn: token("warn"),
      },
      fontFamily: {
        sans: ["'Atkinson Hyperlegible Next Variable'", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
      // Modular scale (ratio 1.25) from a 16px base.
      fontSize: {
        xs: ["0.8125rem", { lineHeight: "1.2rem" }],
        sm: ["0.9375rem", { lineHeight: "1.4rem" }],
        base: ["1rem", { lineHeight: "1.6rem" }],
        lg: ["1.25rem", { lineHeight: "1.9rem" }],
        xl: ["1.5625rem", { lineHeight: "2.1rem" }],
        "2xl": ["1.953rem", { lineHeight: "2.4rem" }],
        "3xl": ["2.441rem", { lineHeight: "2.8rem", letterSpacing: "-0.01em" }],
        "4xl": ["3.052rem", { lineHeight: "3.3rem", letterSpacing: "-0.02em" }],
      },
      borderRadius: {
        control: "8px",
        panel: "10px",
        card: "14px",
      },
      maxWidth: {
        prose: "34rem",
      },
    },
  },
  plugins: [],
} satisfies Config;
