"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, RefreshCw, ShieldAlert, Sparkles } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { toast } from "sonner";

import { CATEGORY_DOT, CategoryBadge } from "@/components/badges";
import { ErrorState, PageHeader, Section } from "@/components/page-header";
import { RouteFunnel } from "@/components/route-funnel";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CATEGORIES, api, unwrap, type Overview } from "@/lib/api/client";
import { dateTime, percent, relativeTime } from "@/lib/format";

type Activity = Overview["activity"][number];

function KpiStrip({ data }: { data: Overview }) {
  const t = useTranslations("overview.kpis");
  const locale = useLocale();
  const items = [
    {
      label: t("sent"),
      value: data.kpis.applications_sent,
      hint: t("sentWeek", { count: data.kpis.sent_last_7_days }),
    },
    { label: t("responseRate"), value: percent(data.kpis.response_rate, locale) },
    { label: t("interviews"), value: data.kpis.interviews },
    { label: t("offers"), value: data.kpis.offers },
  ];
  return (
    <dl className="grid grid-cols-2 border-y sm:grid-cols-4 sm:divide-x">
      {items.map((item, i) => (
        <div
          key={item.label}
          className={`py-4 sm:px-5 sm:first:pl-0 ${i < 2 ? "border-b sm:border-b-0" : ""}`}
        >
          <dt className="text-sm text-muted-foreground">{item.label}</dt>
          <dd className="mt-1 flex items-baseline gap-2">
            <span className="text-2xl font-semibold tabular-nums">{item.value}</span>
            {item.hint ? <span className="text-xs text-muted-foreground">{item.hint}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ActivityLine({ item: initial }: { item: Activity }) {
  let item = initial;
  const t = useTranslations("overview.events");
  const ts = useTranslations("statuses");
  const tc = useTranslations("categories");
  const locale = useLocale();
  const d = item.detail as Record<string, string | undefined>;
  let text: string;
  switch (item.kind) {
    case "status_changed":
      text = t("status_changed", {
        from: ts((d.from ?? "sent") as never),
        to: ts((d.to ?? "sent") as never),
      });
      break;
    case "email_received":
      text = t("email_received", { category: tc((d.category ?? "Autre") as never) });
      break;
    case "created":
    case "email_linked":
    case "edited":
      text = t(item.kind);
      break;
    case "triage": {
      const r = item.detail as { status?: string; classified?: number; flagged?: number };
      text = t("triage");
      item = {
        ...item,
        title:
          r.status === "failed"
            ? t("triageFailed")
            : t("triageResult", { classified: r.classified ?? 0, flagged: r.flagged ?? 0 }),
      };
      break;
    }
    default:
      text = item.kind;
  }
  const body = (
    <>
      <span className="font-medium">{text}</span>
      <span className="truncate text-muted-foreground"> — {item.title}</span>
    </>
  );
  return (
    <li className="flex items-baseline justify-between gap-4 py-2.5 text-sm">
      {item.application_id ? (
        <Link
          href={`/applications?open=${item.application_id}`}
          className="min-w-0 truncate hover:underline"
        >
          {body}
        </Link>
      ) : (
        <span className="min-w-0 truncate">{body}</span>
      )}
      <time dateTime={item.at} className="shrink-0 text-xs text-muted-foreground">
        {relativeTime(item.at, locale)}
      </time>
    </li>
  );
}

function InboxBar({ inbox }: { inbox: Record<string, number> }) {
  const total = Object.values(inbox).reduce((a, b) => a + b, 0);
  if (!total) return null;
  return (
    <div className="space-y-3">
      <div aria-hidden className="flex h-2 overflow-hidden rounded-full bg-muted">
        {CATEGORIES.map((c) =>
          inbox[c] ? (
            <span
              key={c}
              className={CATEGORY_DOT[c]}
              style={{ width: `${(inbox[c] / total) * 100}%` }}
            />
          ) : null,
        )}
      </div>
      <ul className="grid grid-cols-2 gap-x-6 gap-y-1.5 2xl:grid-cols-3">
        {CATEGORIES.map((c) => (
          <li key={c} className="flex items-center justify-between gap-2">
            <Link href={`/inbox?category=${c}`} className="hover:underline">
              <CategoryBadge category={c} />
            </Link>
            <span className="text-sm text-muted-foreground tabular-nums">{inbox[c] ?? 0}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function OverviewPage() {
  const t = useTranslations("overview");
  const tc = useTranslations("common");
  const locale = useLocale();
  const queryClient = useQueryClient();
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: () => unwrap(api.GET("/api/v1/overview")),
    refetchInterval: 60_000,
  });
  const sync = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/gmail/triage", { params: { query: { sync: true } } })),
    onSuccess: () => toast.info(t("syncing")),
    onError: (e) => toast.error(String(e)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["overview"] }),
  });

  const data = overview.data;
  return (
    <>
      <PageHeader
        title={t("title")}
        description={
          data?.last_sync_at
            ? t("lastSync", { when: relativeTime(data.last_sync_at, locale) })
            : t("neverSynced")
        }
        actions={
          <Button variant="outline" onClick={() => sync.mutate()} disabled={sync.isPending}>
            <RefreshCw aria-hidden className={`size-4 ${sync.isPending ? "animate-spin" : ""}`} />
            {sync.isPending ? t("syncing") : t("syncNow")}
          </Button>
        }
      />

      {overview.isError ? (
        <ErrorState
          message={tc("loadError")}
          onRetry={() => overview.refetch()}
          retryLabel={tc("retry")}
        />
      ) : !data ? (
        <div className="space-y-8" aria-busy="true">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : (
        <div className="space-y-10">
          <KpiStrip data={data} />

          <Section title={t("route.title")}>
            <RouteFunnel steps={data.funnel} />
          </Section>

          <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] [&>*]:min-w-0">
            <div className="space-y-10">
              {data.pending_suggestions > 0 || data.security_alerts > 0 ? (
                <Section title={t("attention")}>
                  <ul className="divide-y rounded-lg border border-signal/40 bg-signal/5">
                    {data.pending_suggestions > 0 ? (
                      <li>
                        <Link
                          href="/applications"
                          className="flex items-center gap-3 p-3 text-sm hover:bg-signal/10"
                        >
                          <Sparkles aria-hidden className="size-4 text-signal" />
                          {t("suggestionsPending", { count: data.pending_suggestions })}
                        </Link>
                      </li>
                    ) : null}
                    {data.security_alerts > 0 ? (
                      <li>
                        <Link
                          href="/security"
                          className="flex items-center gap-3 p-3 text-sm hover:bg-signal/10"
                        >
                          <ShieldAlert aria-hidden className="size-4 text-signal" />
                          {t("alerts", { count: data.security_alerts })}
                        </Link>
                      </li>
                    ) : null}
                  </ul>
                </Section>
              ) : null}

              <Section title={t("upcoming")}>
                {data.upcoming_interviews.length ? (
                  <ul className="divide-y">
                    {data.upcoming_interviews.map((i) => (
                      <li key={i.application_id} className="flex items-start gap-3 py-2.5">
                        <CalendarClock aria-hidden className="mt-0.5 size-4 text-cat-entretien" />
                        <Link
                          href={`/applications?open=${i.application_id}`}
                          className="min-w-0 hover:underline"
                        >
                          <span className="block truncate text-sm font-medium">{i.role_title}</span>
                          <span className="block text-sm text-muted-foreground">
                            {i.company ?? "—"} · {dateTime(i.interview_at, locale)}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">{t("noUpcoming")}</p>
                )}
              </Section>

              <Section title={t("inbox")}>
                <InboxBar inbox={data.inbox} />
              </Section>
            </div>

            <Section title={t("activity")}>
              {data.activity.length ? (
                <ul className="divide-y">
                  {data.activity.map((a, i) => (
                    <ActivityLine key={`${a.kind}-${a.at}-${i}`} item={a} />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">{t("noActivity")}</p>
              )}
            </Section>
          </div>
        </div>
      )}
    </>
  );
}
