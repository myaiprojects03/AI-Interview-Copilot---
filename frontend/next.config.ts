import type { NextConfig } from "next";

// Security headers for every page. The API and WebSocket origins come from NEXT_PUBLIC_API_URL,
// which is fixed at build time, so the policy lists exactly the one backend the app talks to.
const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const ws = api.replace(/^http/, "ws");
const dev = process.env.NODE_ENV !== "production";

const csp = [
  "default-src 'self'",
  // Next.js injects small inline scripts; development also needs eval for fast refresh.
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${api} ${ws}`,
  "media-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // Microphone and tab sharing are needed by the copilot on this site only; nothing else.
  { key: "Permissions-Policy", value: "microphone=(self), display-capture=(self), camera=(), geolocation=(), payment=()" },
  ...(dev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  // The Docker image sets NEXT_OUTPUT=standalone: a self-contained server without node_modules.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
