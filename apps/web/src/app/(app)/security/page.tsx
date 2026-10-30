"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";

import { CategoryBadge } from "@/components/badges";
import { ErrorState, PageHeader, Section } from "@/components/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { api, unwrap, type SecurityReport } from "@/lib/api/client";
import { percent, relativeTime } from "@/lib/format";

type Layer = "heuristic" | "guard" | "model" | "user";
type Kind = "direct" | "hidden" | "indirect";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

function Evals({ reports }: { reports: SecurityReport["eval_reports"] }) {
  const t = useTranslations("security");
  const locale = useLocale();
  const triage = reports.triage_eval as Record<string, unknown> | undefined;
  const injection = reports.injection_eval as
    { combined?: { precision: number; recall: number }; generated_at?: string } | undefined;
  if (!triage && !injection) return <p className="text-sm text-muted-foreground">{t("noEvals")}</p>;
  return (
    <dl className="grid grid-cols-2 gap-6 sm:grid-cols-4">
      {triage ? (
        <>
          <Metric label={t("triageAccuracy")} value={percent(Number(triage.accuracy), locale)} />
          <Metric label={t("toolCalls")} value={String(triage.avg_tool_calls_per_batch ?? "—")} />
          <Metric
            label={t("tokens")}
            value={new Intl.NumberFormat(locale).format(Number(triage.avg_tokens_per_batch ?? 0))}
          />
        </>
      ) : null}
      {injection?.combined ? (
        <Metric
          label={t("guardPrecision")}
          value={`${percent(injection.combined.precision, locale)} / ${percent(injection.combined.recall, locale)}`}
        />
      ) : null}
    </dl>
  );
}

export default function SecurityPage() {
  const t = useTranslations("security");
  const tc = useTranslations("common");
  const tl = useTranslations("security.layers");
  const tk = useTranslations("security.kinds");
  const locale = useLocale();
  const report = useQuery({
    queryKey: ["security"],
    queryFn: () => unwrap(api.GET("/api/v1/security")),
  });
  const data = report.data;

  return (
    <>
      <PageHeader title={t("title")} description={t("subtitle")} />
      {report.isError ? (
        <ErrorState
          message={tc("loadError")}
          onRetry={() => report.refetch()}
          retryLabel={tc("retry")}
        />
      ) : !data ? (
        <div className="space-y-4" aria-busy="true">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        <div className="space-y-10">
          <Section
            title={t("flagged")}
            aside={
              <span className="text-sm text-muted-foreground tabular-nums">
                {data.flagged.length}
              </span>
            }
          >
            {data.flagged.length ? (
              <ul className="divide-y border-y">
                {data.flagged.map((item) => (
                  <li
                    key={`${item.kind}-${item.id}`}
                    className="grid gap-2 py-3 sm:grid-cols-[1fr_auto]"
                  >
                    <div className="min-w-0">
                      <Link
                        href={`/inbox?open=${item.id}`}
                        className="block truncate text-sm font-medium hover:underline"
                      >
                        {item.title || "—"}
                      </Link>
                      <p className="truncate text-xs text-muted-foreground">
                        {item.source} · {relativeTime(item.received_at, locale)}
                      </p>
                      <ul className="mt-2 flex flex-wrap gap-1.5">
                        {item.reasons.map((r, i) => (
                          <li
                            key={i}
                            className="rounded border border-signal/40 px-1.5 py-0.5 text-xs text-signal"
                          >
                            {tl((r.layer as Layer) ?? "heuristic")}: {String(r.rule)}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div className="flex items-start gap-4 sm:flex-col sm:items-end sm:gap-1">
                      <span className="text-sm font-semibold text-signal tabular-nums">
                        {t("score", { score: (item.score ?? 0).toFixed(2) })}
                      </span>
                      <CategoryBadge category={item.category} />
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{t("noFlagged")}</p>
            )}
          </Section>

          <Section title={t("suite")}>
            <p className="mb-4 text-sm text-muted-foreground">
              {t("suiteHint", { injections: data.suite.injections, benign: data.suite.benign })}
            </p>
            <dl className="grid grid-cols-2 gap-6 sm:grid-cols-4">
              <Metric label={t("precision")} value={percent(data.suite.precision, locale)} />
              <Metric label={t("recall")} value={percent(data.suite.recall, locale)} />
            </dl>
            <h3 className="mt-6 mb-2 text-sm font-semibold">{t("byKind")}</h3>
            <ul className="space-y-2">
              {Object.entries(data.suite.by_kind).map(([kind, v]) => (
                <li
                  key={kind}
                  className="grid grid-cols-[6rem_1fr_auto] items-center gap-3 text-sm"
                >
                  <span>{tk(kind as Kind)}</span>
                  <span aria-hidden className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full bg-primary"
                      style={{ width: `${(v.detected / Math.max(1, v.total)) * 100}%` }}
                    />
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {t("detected", { detected: v.detected, total: v.total })}
                  </span>
                </li>
              ))}
            </ul>
          </Section>

          <Section title={t("evals")}>
            <Evals reports={data.eval_reports} />
          </Section>
        </div>
      )}
    </>
  );
}
