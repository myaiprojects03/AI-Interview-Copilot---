// Single source of branding. Rebrand the app by editing this file only: name, copy and colours.
// Colours become CSS variables (see app/layout.tsx) that every component and Tailwind class uses.
export const brand = {
  appName: "Interview Copilot",
  tagline: "Your own experience, ready the moment they ask.",
  description:
    "Interview Copilot listens to your video interview, catches each question and puts a short answer " +
    "built from your CV on screen, in about two seconds.",
  // Shown on the Privacy and Terms pages for questions about data. Leave empty to omit.
  contactEmail: "",
  colors: {
    ink: "#0F1729",        // workspace background
    panel: "#172036",      // panels and inputs
    line: "#26314A",       // borders and dividers
    text: "#E8ECF4",       // main text on dark
    muted: "#8C97AD",      // secondary text on dark
    paper: "#F5F7FB",      // the cue card the answer is written on
    paperInk: "#141C2E",   // text on the cue card
    paperMuted: "#5B6478", // secondary text on the cue card
    accent: "#F5C84C",     // highlighter: key lines, main buttons, focus ring
    accentInk: "#1B1606",  // text on the highlighter colour
    live: "#E5484D",       // the "live" tally light, and destructive actions
    warn: "#F0A647",       // warnings
  },
} as const;

export type Brand = typeof brand;

/** CSS custom properties for the colours, applied on <html>. */
export function brandCssVars(): Record<string, string> {
  return Object.fromEntries(Object.entries(brand.colors).map(([k, v]) => [`--c-${k}`, v]));
}
