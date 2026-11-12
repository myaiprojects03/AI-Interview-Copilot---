import type { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import GoogleProvider from "next-auth/providers/google";

// Server-side only. The backend checks passwords and issues the access token; Auth.js keeps it in an
// encrypted, httpOnly session cookie and exposes it to the app through the session.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const TOKEN_DAYS = 7;

export const googleEnabled = Boolean(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET);

type BackendAuth = { user: { id: string; email: string; name: string }; access_token: string };

async function backend(path: string, body: unknown, headers: Record<string, string> = {}): Promise<BackendAuth> {
  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new Error("Cannot reach the server. Please try again in a moment.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Sign-in failed.");
  return data as BackendAuth;
}

export const authOptions: NextAuthOptions = {
  session: { strategy: "jwt", maxAge: TOKEN_DAYS * 24 * 60 * 60 },
  pages: { signIn: "/login" },
  providers: [
    CredentialsProvider({
      name: "Email",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
        name: { label: "Name", type: "text" },
        mode: { label: "Mode", type: "text" }, // "login" | "register"
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials.password) throw new Error("Enter your email and password.");
        const register = credentials.mode === "register";
        const out = await backend(register ? "/api/auth/register" : "/api/auth/login", {
          email: credentials.email,
          password: credentials.password,
          ...(register ? { name: credentials.name ?? "" } : {}),
        });
        return { id: out.user.id, email: out.user.email, name: out.user.name, accessToken: out.access_token };
      },
    }),
    ...(googleEnabled
      ? [GoogleProvider({ clientId: process.env.GOOGLE_CLIENT_ID!, clientSecret: process.env.GOOGLE_CLIENT_SECRET! })]
      : []),
  ],
  callbacks: {
    async signIn({ account, profile }) {
      // Only accept Google accounts whose email Google has verified.
      if (account?.provider === "google") {
        return Boolean((profile as { email_verified?: boolean } | undefined)?.email_verified);
      }
      return true;
    },
    async jwt({ token, user, account, profile }) {
      if (account?.provider === "google" && profile?.email) {
        const out = await backend(
          "/api/auth/google",
          { email: profile.email, name: profile.name ?? "", google_sub: account.providerAccountId },
          { "X-Internal-Secret": process.env.INTERNAL_API_SECRET ?? "" },
        );
        token.uid = out.user.id;
        token.accessToken = out.access_token;
        token.accessTokenExpires = Date.now() + TOKEN_DAYS * 86_400_000;
      } else if (user) {
        token.uid = user.id;
        token.accessToken = user.accessToken;
        token.accessTokenExpires = Date.now() + TOKEN_DAYS * 86_400_000;
      }
      return token;
    },
    async session({ session, token }) {
      session.user.id = token.uid as string;
      session.accessToken = token.accessToken as string;
      session.expired = Date.now() > ((token.accessTokenExpires as number) ?? 0);
      return session;
    },
  },
};
