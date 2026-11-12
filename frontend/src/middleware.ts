import { withAuth } from "next-auth/middleware";

// Signed-out visitors to these pages are sent to /login and returned afterwards.
export default withAuth({ pages: { signIn: "/login" } });

export const config = { matcher: ["/copilot/:path*", "/account/:path*", "/dashboard/:path*", "/admin/:path*"] };
