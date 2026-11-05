"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BriefcaseBusiness,
  FileText,
  FileUser,
  Inbox,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  Send,
  Settings,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { LocaleSwitcher } from "@/components/locale-switcher";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { api, setCsrfToken, unwrap } from "@/lib/api/client";
import { cn } from "@/lib/utils";

type NavKey =
  | "overview"
  | "inbox"
  | "applications"
  | "jobs"
  | "resume"
  | "templates"
  | "review"
  | "security"
  | "settings";

interface NavItem {
  key: NavKey;
  href: string;
  icon: LucideIcon;
  soon?: boolean;
}

const NAV: NavItem[][] = [
  [
    { key: "overview", href: "/", icon: LayoutDashboard },
    { key: "inbox", href: "/inbox", icon: Inbox },
    { key: "applications", href: "/applications", icon: BriefcaseBusiness },
  ],
  [
    { key: "jobs", href: "/jobs", icon: Search },
    { key: "resume", href: "/resume", icon: FileUser },
    { key: "templates", href: "/templates", icon: FileText },
    { key: "review", href: "/review", icon: Send, soon: true },
  ],
  [
    { key: "security", href: "/security", icon: ShieldAlert },
    { key: "settings", href: "/settings", icon: Settings },
  ],
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: () => unwrap(api.GET("/api/v1/overview")),
    staleTime: 30_000,
  });
  const badges: Partial<Record<NavKey, number>> = {
    security: overview.data?.security_alerts,
    applications: overview.data?.pending_suggestions,
  };

  return (
    <nav aria-label="JobPilot" className="flex flex-col gap-5">
      {NAV.map((group, i) => (
        <ul key={i} className="flex flex-col gap-0.5">
          {group.map(({ key, href, icon: Icon, soon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            const badge = badges[key];
            return (
              <li key={key}>
                <Link
                  href={href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-2.5 py-2 text-sm transition-colors",
                    "hover:bg-sidebar-accent focus-visible:bg-sidebar-accent",
                    active
                      ? "bg-sidebar-accent font-semibold text-sidebar-foreground"
                      : "text-muted-foreground",
                    soon && "opacity-60",
                  )}
                >
                  <Icon
                    aria-hidden
                    className={cn("size-4 shrink-0", active && "text-sidebar-primary")}
                  />
                  <span className="flex-1 truncate">{t(key)}</span>
                  {soon ? (
                    <span className="text-[0.7rem] text-muted-foreground">{t("soon")}</span>
                  ) : badge ? (
                    <span className="min-w-5 rounded-full bg-signal px-1.5 text-center text-[0.7rem] leading-5 font-semibold text-signal-foreground">
                      {badge}
                    </span>
                  ) : null}
                </Link>
              </li>
            );
          })}
        </ul>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2 px-2.5 text-[0.95rem] font-semibold">
      {/* a route with three stops — the product in one glyph */}
      <svg viewBox="0 0 24 24" aria-hidden className="size-5 text-primary">
        <path
          d="M3 17 C 8 17, 9 7, 14 7 S 19 12, 21 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <circle cx="3" cy="17" r="2" fill="currentColor" />
        <circle cx="14" cy="7" r="2" fill="currentColor" />
        <circle cx="21" cy="12" r="2" fill="currentColor" />
      </svg>
      JobPilot
    </Link>
  );
}

function Footer() {
  const t = useTranslations("nav");
  const router = useRouter();
  const queryClient = useQueryClient();
  async function logout() {
    await api.POST("/api/v1/auth/logout");
    setCsrfToken(null);
    queryClient.clear();
    router.replace("/login");
  }
  return (
    <div className="flex items-center justify-between gap-1 border-t pt-3">
      <LocaleSwitcher />
      <div className="flex items-center">
        <ThemeToggle />
        <Button
          variant="ghost"
          size="icon"
          onClick={logout}
          aria-label={t("logout")}
          title={t("logout")}
        >
          <LogOut aria-hidden className="size-4" />
        </Button>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const t = useTranslations("nav");
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        {t("skip")}
      </a>

      <aside className="sticky top-0 hidden h-dvh flex-col gap-6 border-r bg-sidebar px-3 py-4 lg:flex">
        <Brand />
        <div className="flex-1 overflow-y-auto">
          <NavLinks />
        </div>
        <Footer />
      </aside>

      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b bg-background/95 px-4 backdrop-blur lg:hidden">
        <Brand />
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger asChild>
            <Button variant="ghost" size="icon" aria-label={t("menu")}>
              <Menu aria-hidden className="size-5" />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="flex w-72 flex-col gap-6 bg-sidebar p-4">
            <SheetTitle className="sr-only">{t("menu")}</SheetTitle>
            <Brand />
            <div className="flex-1 overflow-y-auto">
              <NavLinks onNavigate={() => setOpen(false)} />
            </div>
            <Footer />
          </SheetContent>
        </Sheet>
      </header>

      <main id="main" className="min-w-0 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
        <div className="mx-auto max-w-[1200px]">{children}</div>
      </main>
    </div>
  );
}
