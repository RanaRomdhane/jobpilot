"use client";

import { useQuery } from "@tanstack/react-query";
import { Inbox } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, unwrap } from "@/lib/api/client";
import { relativeTime } from "@/lib/format";
import type { QueueItem } from "@/lib/review";
import { cn } from "@/lib/utils";

import { ReviewDetail } from "./review-detail";

/** Applications with documents on the left, the selected one's review on the right. */
export function ReviewScreen() {
  const t = useTranslations("review");
  const tc = useTranslations("common");
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const selected = Number(params.get("application")) || null;

  const queue = useQuery({
    queryKey: ["outgoing", "queue"],
    queryFn: () => unwrap(api.GET("/api/v1/outgoing/queue")),
  });

  const select = (id: number) => router.replace(`${pathname}?application=${id}`, { scroll: false });

  if (queue.isPending) {
    return (
      <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
        <Skeleton className="h-64" />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (queue.isError) {
    return (
      <div role="alert" className="rounded-lg border border-dashed p-8 text-center">
        <p className="font-medium">{t("queueError")}</p>
        <Button className="mt-4" variant="outline" onClick={() => void queue.refetch()}>
          {tc("retry")}
        </Button>
      </div>
    );
  }
  const items = queue.data ?? [];
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-8 text-center">
        <Inbox aria-hidden className="mx-auto size-8 text-muted-foreground" />
        <p className="mt-3 font-medium">{t("empty.title")}</p>
        <p className="mt-1 text-sm text-muted-foreground">{t("empty.body")}</p>
        <Button asChild className="mt-4">
          <Link href="/jobs">{t("empty.action")}</Link>
        </Button>
      </div>
    );
  }
  const current = selected ?? items[0]?.application_id ?? null;
  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <nav aria-label={t("queue")} className="lg:sticky lg:top-4 lg:self-start">
        <ul className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
          {items.map((item) => (
            <li key={item.application_id} className="min-w-56 lg:min-w-0">
              <QueueButton
                item={item}
                active={item.application_id === current}
                onSelect={() => select(item.application_id)}
              />
            </li>
          ))}
        </ul>
      </nav>
      {current ? <ReviewDetail key={current} applicationId={current} /> : null}
    </div>
  );
}

function QueueButton({
  item,
  active,
  onSelect,
}: {
  item: QueueItem;
  active: boolean;
  onSelect: () => void;
}) {
  const t = useTranslations("review");
  const ts = useTranslations("documents.status");
  const locale = useLocale();
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "w-full rounded-md border px-3 py-2 text-left transition-colors hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        active && "border-primary bg-muted/60",
      )}
    >
      <p className="truncate text-sm font-medium">{item.role_title}</p>
      <p className="truncate text-xs text-muted-foreground">
        {item.company ?? t("unknownCompany")} · {relativeTime(item.updated_at, locale)}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        <Badge variant="secondary">{ts(item.status)}</Badge>
        {item.email_status ? (
          <Badge variant={item.email_status === "failed" ? "destructive" : "outline"}>
            {t(`emailStatus.${item.email_status}`)}
          </Badge>
        ) : null}
      </div>
    </button>
  );
}
