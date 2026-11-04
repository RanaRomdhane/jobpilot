"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleMinus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { api, unwrap } from "@/lib/api/client";
import { relativeTime } from "@/lib/format";
import { safeHref, type SourceStatus } from "@/lib/jobs";

const KNOWN_ERRORS = [
  "daily_cap",
  "robots_disallow",
  "network",
  "parse_empty",
  "too_large",
  "bad_json",
  "internal",
] as const;

function Status({ s }: { s: SourceStatus }) {
  const t = useTranslations("jobs.sources");
  const locale = useLocale();
  const run = s.last_run as {
    ok?: boolean;
    error?: string;
    at?: string;
    fetched?: number;
    new?: number;
    calls_today?: number;
  } | null;
  if (!s.enabled) {
    return (
      <span className="flex items-start gap-1.5 text-sm text-muted-foreground">
        <CircleMinus aria-hidden className="mt-0.5 size-4 shrink-0" />
        <span>
          {t("disabled")}
          <span className="block text-xs">{t("disabledHint")}</span>
        </span>
      </span>
    );
  }
  if (!run) return <span className="text-sm text-muted-foreground">{t("never")}</span>;
  const error = run.error ?? "";
  const message = run.ok
    ? t("ok")
    : error.startsWith("http_")
      ? t("errors.http", { code: error.slice(5) })
      : (KNOWN_ERRORS as readonly string[]).includes(error)
        ? t(`errors.${error as "network"}`)
        : error;
  return (
    <span className="flex items-start gap-1.5 text-sm">
      {run.ok ? (
        <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-success" />
      ) : (
        <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-signal" />
      )}
      <span>
        {message}
        <span className="block text-xs text-muted-foreground">
          {t("lastRun", { when: relativeTime(run.at, locale) })} ·{" "}
          {t("counts", {
            fetched: run.fetched ?? 0,
            new: run.new ?? 0,
            calls: run.calls_today ?? 0,
          })}
        </span>
      </span>
    </span>
  );
}

export function SourcesDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const t = useTranslations("jobs.sources");
  const sources = useQuery({
    queryKey: ["jobs", "sources"],
    queryFn: () => unwrap(api.GET("/api/v1/jobs/sources")),
    enabled: open,
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        {sources.data ? (
          <ul className="divide-y">
            {sources.data.map((s) => (
              <li key={s.name} className="grid gap-2 py-3 sm:grid-cols-[11rem_minmax(0,1fr)]">
                <div>
                  <a
                    href={safeHref(s.homepage) ?? undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-medium hover:underline"
                  >
                    {s.display_name}
                  </a>
                  <p className="text-xs text-muted-foreground">
                    {t(`kind.${s.kind as "api"}`)} · {t("stored", { count: s.job_count })}
                  </p>
                </div>
                <Status s={s} />
              </li>
            ))}
          </ul>
        ) : (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
