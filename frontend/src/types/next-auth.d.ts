import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  interface Session {
    accessToken: string;
    expired: boolean;
    user: { id: string; email?: string | null; name?: string | null; image?: string | null };
  }
  interface User {
    accessToken?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    uid?: string;
    accessToken?: string;
    accessTokenExpires?: number;
  }
}
