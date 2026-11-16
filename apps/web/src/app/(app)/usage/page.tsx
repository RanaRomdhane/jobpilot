"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { ErrorState, PageHeader, Section } from "@/components/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, unwrap } from "@/lib/api/client";
import { percent, shortDate } from "@/lib/format";
import {
  PERIODS,
  type Period,
  type Usage,
  barHeights,
  cacheShare,
  compactTokens,
  usd,
} from "@/lib/usage";

const PURPOSES = ["triage", "guard", "parse_resume", "rerank", "tailor", "cover_letter"] as const;

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
      {hint ? <dd className="text-xs text-muted-foreground">{hint}</dd> : null}
    </div>
  );
}

function Today({ data }: { data: Usage }) {
  const t = useTranslations("usage");
  const locale = useLocale();
  const { tokens, daily_limit: limit } = data.today;
  if (!limit) {
    return (
      <p className="text-sm">{t("todayNoLimit", { tokens: compactTokens(tokens, locale) })}</p>
    );
  }
  const share = Math.min(1, tokens / limit);
  return (
    <div className="max-w-xl space-y-1.5">
      <div
        role="meter"
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={Math.min(tokens, limit)}
        aria-label={t("today")}
        className="h-2 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${share * 100}%` }} />
      </div>
      <p className="text-sm text-muted-foreground">
        {t("todayValue", {
          tokens: compactTokens(tokens, locale),
          limit: compactTokens(limit, locale),
          share: percent(share, locale),
        })}
      </p>
    </div>
  );
}

function Days({ data }: { data: Usage }) {
  const t = useTranslations("usage");
  const locale = useLocale();
  const heights = barHeights(data.by_day);
  if (heights.every((h) => h === 0)) {
    return <p className="text-sm text-muted-foreground">{t("noCalls")}</p>;
  }
  const first = data.by_day[0];
  const last = data.by_day[data.by_day.length - 1];
  return (
    <figure>
      <ol className="flex h-32 items-end gap-px" aria-label={t("perDay")}>
        {data.by_day.map((d, i) => (
          <li
            key={d.day}
            className="group relative flex h-full min-w-0 flex-1 items-end"
            title={t("dayTitle", {
              day: shortDate(d.day, locale),
              tokens: compactTokens(d.tokens, locale),
              calls: d.calls,
            })}
          >
            <span
              className="w-full rounded-t-sm bg-primary/80 group-hover:bg-primary"
              style={{ height: `${heights[i]}%` }}
            />
            <span className="sr-only">
              {t("dayTitle", {
                day: shortDate(d.day, locale),
                tokens: compactTokens(d.tokens, locale),
                calls: d.calls,
              })}
            </span>
          </li>
        ))}
      </ol>
      <figcaption className="mt-1 flex justify-between text-xs text-muted-foreground">
        <span>{first ? shortDate(first.day, locale) : ""}</span>
        <span>{last ? shortDate(last.day, locale) : ""}</span>
      </figcaption>
    </figure>
  );
}

function Purposes({ data }: { data: Usage }) {
  const t = useTranslations("usage");
  const tp = useTranslations("settings.purposes");
  const locale = useLocale();
  const nf = new Intl.NumberFormat(locale);
  const name = (p: string) =>
    (PURPOSES as readonly string[]).includes(p) ? tp(p as (typeof PURPOSES)[number]) : p;
  if (!data.by_purpose.length) {
    return <p className="text-sm text-muted-foreground">{t("noCalls")}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("cols.purpose")}</TableHead>
            <TableHead>{t("cols.model")}</TableHead>
            <TableHead className="text-right">{t("cols.calls")}</TableHead>
            <TableHead className="text-right">{t("cols.cached")}</TableHead>
            <TableHead className="text-right">{t("cols.input")}</TableHead>
            <TableHead className="text-right">{t("cols.output")}</TableHead>
            <TableHead className="text-right">{t("cols.latency")}</TableHead>
            <TableHead className="text-right">{t("cols.cost")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.by_purpose.map((p) => (
            <TableRow key={`${p.purpose}-${p.provider}-${p.model}`}>
              <TableCell className="font-medium">{name(p.purpose)}</TableCell>
              <TableCell className="text-muted-foreground">{p.model}</TableCell>
              <TableCell className="text-right tabular-nums">
                {nf.format(p.calls)}
                {p.errors ? (
                  <span className="ml-1 text-xs text-destructive">
                    {t("errors", { n: p.errors })}
                  </span>
                ) : null}
              </TableCell>
              <TableCell className="text-right tabular-nums">{nf.format(p.cached_calls)}</TableCell>
              <TableCell className="text-right tabular-nums">{nf.format(p.input_tokens)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {nf.format(p.output_tokens)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {t("ms", { n: nf.format(p.avg_latency_ms) })}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {p.cost != null ? usd(p.cost, locale) : "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export default function Page() {
  const t = useTranslations("usage");
  const tc = useTranslations("common");
  const nav = useTranslations("nav");
  const locale = useLocale();
  const [days, setDays] = useState<Period>(30);
  const usage = useQuery({
    queryKey: ["usage", days],
    queryFn: () => unwrap(api.GET("/api/v1/usage", { params: { query: { days } } })),
  });
  const data = usage.data;
  const share = data ? cacheShare(data.totals.calls, data.totals.cached_calls) : null;

  return (
    <>
      <PageHeader
        title={nav("usage")}
        description={t("intro")}
        actions={
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v) as Period)}>
            <SelectTrigger aria-label={t("period")} className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PERIODS.map((p) => (
                <SelectItem key={p} value={String(p)}>
                  {t("lastDays", { n: p })}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      {usage.isPending ? (
        <Skeleton className="h-96" />
      ) : usage.isError || !data ? (
        <ErrorState
          message={tc("loadError")}
          onRetry={() => void usage.refetch()}
          retryLabel={tc("retry")}
        />
      ) : (
        <div className="space-y-8">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Metric
              label={t("calls")}
              value={new Intl.NumberFormat(locale).format(data.totals.calls)}
              hint={data.totals.errors ? t("errors", { n: data.totals.errors }) : undefined}
            />
            <Metric
              label={t("tokens")}
              value={compactTokens(data.totals.input_tokens + data.totals.output_tokens, locale)}
              hint={t("inOut", {
                input: compactTokens(data.totals.input_tokens, locale),
                output: compactTokens(data.totals.output_tokens, locale),
              })}
            />
            <Metric
              label={t("cache")}
              value={share == null ? "—" : percent(share, locale)}
              hint={t("cacheHint", { n: data.totals.cached_calls })}
            />
            <Metric
              label={t("cost")}
              value={data.totals.cost != null ? usd(data.totals.cost, locale) : "—"}
              hint={data.totals.cost != null ? t("costHint") : t("noPrices")}
            />
          </dl>

          {data.unpriced_models.length && data.totals.cost != null ? (
            <p className="text-sm text-muted-foreground">
              {t("unpriced", { models: data.unpriced_models.join(", ") })}
            </p>
          ) : null}

          <Section title={t("today")}>
            <Today data={data} />
          </Section>
          <Section
            title={t("perDay")}
            aside={
              <span className="text-xs text-muted-foreground">
                {t("waited", { s: data.totals.waited_s })}
              </span>
            }
          >
            <Days data={data} />
          </Section>
          <Section title={t("perPurpose")}>
            <Purposes data={data} />
          </Section>
        </div>
      )}
    </>
  );
}
