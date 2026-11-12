"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut, useSession } from "next-auth/react";
import { cn } from "@/components/ui/cn";
import { LogoMark } from "@/components/ui/Misc";
import { brand } from "@/config/brand";

const NAV = [
  { href: "/copilot", label: "Copilot" },
  { href: "/dashboard", label: "Dashboard" },
];

export function Wordmark({ compact }: { compact?: boolean }) {
  return (
    <Link href="/" className="flex flex-none items-center gap-2 font-bold" aria-label={brand.appName}>
      <LogoMark />
      {/* In the app bar the name is hidden on phones to leave room for navigation. */}
      <span className={compact ? "hidden sm:inline" : undefined}>{brand.appName}</span>
    </Link>
  );
}

export function TopBar({ right }: { right?: React.ReactNode }) {
  const pathname = usePathname();
  const { data: session } = useSession();
  return (
    <header className="border-b border-line">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 sm:gap-6 sm:px-6">
        <Wordmark compact />
        <nav aria-label="Main" className="flex items-center gap-1">
          {NAV.map((item) => {
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-control px-3 py-1.5 text-sm",
                  active ? "bg-panel font-semibold text-text" : "text-muted hover:text-text",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3 sm:gap-4">
          {right}
          {session?.user && (
            <div className="flex items-center gap-3 text-sm">
              <Link href="/account" className="text-muted hover:text-text" title="Account">
                <span className="hidden sm:inline">{session.user.name || session.user.email}</span>
                <span className="sm:hidden">Account</span>
              </Link>
              {/* On phones Sign out lives on the Account page, so the bar fits beside the live status. */}
              <button
                className="hidden whitespace-nowrap text-muted hover:text-text sm:inline"
                onClick={() => signOut({ callbackUrl: "/login" })}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

export function PageShell({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-screen">
      <TopBar />
      <main className={cn("mx-auto px-4 py-8 sm:px-6", wide ? "max-w-6xl" : "max-w-4xl")}>{children}</main>
    </div>
  );
}
