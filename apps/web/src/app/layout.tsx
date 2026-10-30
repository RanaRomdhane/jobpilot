import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { LocaleSwitcher } from "@/components/locale-switcher";
import { Providers } from "@/components/providers";
import { ThemeToggle } from "@/components/theme-toggle";

import "./globals.css";

const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("app");
  return { title: t("name"), description: t("tagline"), robots: { index: false, follow: false } };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  const t = await getTranslations("app");
  return (
    <html
      lang={locale}
      className={`${geistSans.variable} ${geistMono.variable}`}
      suppressHydrationWarning
    >
      <body className="antialiased">
        <NextIntlClientProvider>
          <Providers>
            <div className="flex min-h-dvh flex-col">
              <header className="border-b">
                <div className="mx-auto flex h-14 w-full max-w-5xl items-center justify-between gap-4 px-4">
                  <span className="font-semibold tracking-tight">{t("name")}</span>
                  <div className="flex items-center gap-2">
                    <LocaleSwitcher />
                    <ThemeToggle />
                  </div>
                </div>
              </header>
              <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">{children}</main>
            </div>
          </Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
