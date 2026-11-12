import type { Metadata } from "next";
import "@fontsource-variable/atkinson-hyperlegible-next";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { brand, brandCssVars } from "@/config/brand";

export const metadata: Metadata = {
  title: { default: brand.appName, template: `%s | ${brand.appName}` },
  description: brand.description,
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" style={brandCssVars() as React.CSSProperties}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
