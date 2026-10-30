"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { CategoryBadge, ConfidenceMeter, SuspiciousMark } from "@/components/badges";
import { EmailDrawer } from "@/components/email-drawer";
import { ErrorState, PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { CATEGORIES, api, unwrap, type Category, type Email } from "@/lib/api/client";
import { relativeTime, senderName } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE = 50;

function useFilters() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    if (!("offset" in patch)) next.delete("offset");
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };
  return {
    category: params.get("category") as Category | null,
    flagged: params.get("flagged") === "1",
    low: params.get("low") === "1",
    q: params.get("q") ?? "",
    offset: Number(params.get("offset") ?? 0),
    open: params.get("open") ? Number(params.get("open")) : null,
    set,
  };
}

function Row({ email, onOpen }: { email: Email; onOpen: () => void }) {
  const t = useTranslations("inbox");
  const tc = useTranslations("common");
  const locale = useLocale();
  return (
    <tr
      className="cursor-pointer border-b transition-colors focus-within:bg-muted/60 hover:bg-muted/60"
      onClick={onOpen}
    >
      <td className="hidden max-w-48 truncate py-2.5 pr-4 text-sm md:table-cell">
        {senderName(email.from_name, email.from_address)}
      </td>
      <td className="max-w-0 py-2.5 pr-4">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpen();
          }}
          className="block w-full truncate text-left text-sm font-medium focus-visible:outline-none"
        >
          {email.subject || "—"}
        </button>
        <span className="block truncate text-xs text-muted-foreground md:hidden">
          {senderName(email.from_name, email.from_address)}
        </span>
        <span className="hidden truncate text-xs text-muted-foreground md:block">
          {email.snippet}
        </span>
      </td>
      <td className="py-2.5 pr-4">
        <div className="flex flex-col items-start gap-1">
          <CategoryBadge category={email.category} />
          {email.injection_flag ? <SuspiciousMark label={t("suspicious")} /> : null}
        </div>
      </td>
      <td className="hidden py-2.5 pr-4 lg:table-cell">
        <ConfidenceMeter value={email.confidence} label={tc("confidence")} />
      </td>
      <td className="py-2.5 text-right text-xs whitespace-nowrap text-muted-foreground">
        {relativeTime(email.received_at, locale)}
      </td>
    </tr>
  );
}

function InboxView() {
  const t = useTranslations("inbox");
  const tc = useTranslations("common");
  const f = useFilters();
  const [search, setSearch] = useState(f.q);

  useEffect(() => {
    const id = setTimeout(() => {
      if (search !== f.q) f.set({ q: search });
    }, 300);
    return () => clearTimeout(id);
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const emails = useQuery({
    queryKey: ["emails", f.category, f.flagged, f.low, f.q, f.offset],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/emails", {
          params: {
            query: {
              category: f.category ? [f.category] : undefined,
              flagged: f.flagged || undefined,
              max_confidence: f.low ? 0.7 : undefined,
              sort: f.low ? "confidence_asc" : "received_desc",
              q: f.q || undefined,
              limit: PAGE,
              offset: f.offset,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  const data = emails.data;
  const total = Object.entries(data?.counts ?? {})
    .filter(([k]) => k !== "flagged")
    .reduce((a, [, n]) => a + n, 0);
  const filtered = Boolean(f.category || f.flagged || f.low || f.q);

  return (
    <>
      <PageHeader title={t("title")} description={t("subtitle")} />

      <div className="mb-4 flex flex-col gap-3">
        <div role="group" aria-label={t("filters.category")} className="flex flex-wrap gap-1.5">
          <Button
            size="sm"
            variant={f.category === null ? "secondary" : "ghost"}
            aria-pressed={f.category === null}
            onClick={() => f.set({ category: null })}
          >
            {tc("all")}
            <span className="text-muted-foreground tabular-nums">{total}</span>
          </Button>
          {CATEGORIES.map((c) => (
            <Button
              key={c}
              size="sm"
              variant={f.category === c ? "secondary" : "ghost"}
              aria-pressed={f.category === c}
              onClick={() => f.set({ category: f.category === c ? null : c })}
            >
              <CategoryBadge category={c} />
              <span className="text-muted-foreground tabular-nums">{data?.counts[c] ?? 0}</span>
            </Button>
          ))}
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative sm:w-80">
            <Label htmlFor="inbox-search" className="sr-only">
              {tc("search")}
            </Label>
            <Search
              aria-hidden
              className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground"
            />
            <Input
              id="inbox-search"
              type="search"
              placeholder={t("filters.search")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-8"
            />
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="flagged"
              checked={f.flagged}
              onCheckedChange={(v) => f.set({ flagged: v ? "1" : null })}
            />
            <Label htmlFor="flagged" className="text-sm font-normal">
              {t("filters.flagged")}
              <span className="ml-1 text-muted-foreground tabular-nums">
                {data?.counts.flagged ?? 0}
              </span>
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="low"
              checked={f.low}
              onCheckedChange={(v) => f.set({ low: v ? "1" : null })}
            />
            <Label htmlFor="low" className="text-sm font-normal">
              {t("filters.lowConfidence")}
            </Label>
          </div>
        </div>
      </div>

      {emails.isError ? (
        <ErrorState
          message={tc("loadError")}
          onRetry={() => emails.refetch()}
          retryLabel={tc("retry")}
        />
      ) : !data ? (
        <div className="space-y-2" aria-busy="true">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : data.items.length === 0 ? (
        <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          {filtered ? t("empty") : t("emptyAll")}
        </p>
      ) : (
        <>
          <div className={cn("overflow-x-auto", emails.isFetching && "opacity-70")}>
            <table className="w-full table-fixed border-collapse">
              <caption className="sr-only">{t("title")}</caption>
              <colgroup>
                <col className="hidden w-48 md:table-column" />
                <col />
                <col className="w-28 sm:w-32" />
                <col className="hidden w-28 lg:table-column" />
                <col className="w-20 sm:w-24" />
              </colgroup>
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th scope="col" className="hidden pb-2 font-medium md:table-cell">
                    {t("columns.from")}
                  </th>
                  <th scope="col" className="pb-2 font-medium">
                    {t("columns.subject")}
                  </th>
                  <th scope="col" className="pb-2 font-medium">
                    {t("columns.category")}
                  </th>
                  <th scope="col" className="hidden pb-2 font-medium lg:table-cell">
                    {t("columns.confidence")}
                  </th>
                  <th scope="col" className="pb-2 text-right font-medium">
                    {t("columns.received")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((email) => (
                  <Row
                    key={email.id}
                    email={email}
                    onOpen={() => f.set({ open: String(email.id), offset: String(f.offset) })}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {tc("pageOf", {
                from: f.offset + 1,
                to: f.offset + data.items.length,
                total: data.total,
              })}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={f.offset === 0}
                onClick={() => f.set({ offset: String(Math.max(0, f.offset - PAGE)) })}
              >
                {tc("previous")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={f.offset + PAGE >= data.total}
                onClick={() => f.set({ offset: String(f.offset + PAGE) })}
              >
                {tc("next")}
              </Button>
            </div>
          </div>
        </>
      )}

      <EmailDrawer
        emailId={f.open}
        onClose={() => f.set({ open: null, offset: String(f.offset) })}
      />
    </>
  );
}

export default function InboxPage() {
  return (
    <Suspense>
      <InboxView />
    </Suspense>
  );
}
