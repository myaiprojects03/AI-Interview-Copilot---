import { redirect } from "next/navigation";
import { getServerSession } from "next-auth";
import { LoginForm } from "@/components/LoginForm";
import { authOptions, googleEnabled } from "@/lib/authOptions";

export default async function LoginPage() {
  const session = await getServerSession(authOptions);
  if (session && !session.expired) redirect("/copilot");
  return <LoginForm googleEnabled={googleEnabled} />;
}
