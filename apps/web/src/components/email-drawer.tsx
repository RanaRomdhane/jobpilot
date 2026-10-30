"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, ShieldCheck } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { CategoryBadge, ConfidenceMeter, SuspiciousMark } from "@/components/badges";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { CATEGORIES, api, unwrap, type Category } from "@/lib/api/client";
import { dateTime, senderName } from "@/lib/format";

type Layer = "heuristic" | "guard" | "model" | "user";

export function EmailDrawer({ emailId, onClose }: { emailId: number | null; onClose: () => void }) {
  const t = useTranslations("inbox");
  const tc = useTranslations("common");
  const tcat = useTranslations("categories");
  const tl = useTranslations("security.layers");
  const locale = useLocale();
  const queryClient = useQueryClient();

  const email = useQuery({
    queryKey: ["email", emailId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/emails/{email_id}", { params: { path: { email_id: emailId! } } })),
    enabled: emailId !== null,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["emails"] });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
    void queryClient.invalidateQueries({ queryKey: ["security"] });
  };

  const reclassify = useMutation({
    mutationFn: (category: Category) =>
      unwrap(
        api.PATCH("/api/v1/emails/{email_id}/category", {
          params: { path: { email_id: emailId! } },
          body: { category },
        }),
      ),
    onSuccess: (res) => {
      queryClient.setQueryData(["email", emailId], res.email);
      toast.success(t("reclassified", { category: tcat(res.email.category ?? "unclassified") }));
      refresh();
    },
    onError: (e) => toast.error(String(e)),
  });

  const clearFlag = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/emails/{email_id}/clear-suspicious", {
          params: { path: { email_id: emailId! } },
        }),
      ),
    onSuccess: (res) => {
      queryClient.setQueryData(["email", emailId], res.email);
      toast.success(t("flagCleared"));
      refresh();
    },
    onError: (e) => toast.error(String(e)),
  });

  const e = email.data;
  const evidence = (e?.injection_reasons ?? []).filter(
    (r) => typeof r.weight === "number" && (r.weight as number) > 0,
  );

  return (
    <Sheet open={emailId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle className="pr-6 text-left text-lg leading-snug">
            {e?.subject || (email.isPending ? "…" : "—")}
          </SheetTitle>
          <SheetDescription className="text-left">
            {e
              ? `${senderName(e.from_name, e.from_address)} · ${dateTime(e.received_at, locale)}`
              : t("detail")}
          </SheetDescription>
        </SheetHeader>

        {!e ? (
          <div className="space-y-3 px-4" aria-busy="true">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : (
          <div className="space-y-6 px-4 pb-6">
            <p className="text-sm leading-relaxed text-pretty">{e.snippet}</p>

            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">{t("columns.category")}</dt>
              <dd className="flex flex-wrap items-center gap-2">
                <CategoryBadge category={e.category} />
                {e.manual_category ? (
                  <span className="text-xs text-muted-foreground">{t("manual")}</span>
                ) : null}
                {e.injection_flag ? <SuspiciousMark label={t("suspicious")} /> : null}
              </dd>
              <dt className="text-muted-foreground">{tc("confidence")}</dt>
              <dd>
                <ConfidenceMeter value={e.confidence} label={tc("confidence")} />
              </dd>
              {e.classification_reason && e.classification_reason !== "manual" ? (
                <>
                  <dt className="text-muted-foreground">{t("reason")}</dt>
                  <dd>{e.classification_reason}</dd>
                </>
              ) : null}
              {typeof e.extracted.interview_at === "string" ? (
                <>
                  <dt className="text-muted-foreground">{tcat("Entretien")}</dt>
                  <dd>{t("interviewAt", { date: dateTime(e.extracted.interview_at, locale) })}</dd>
                </>
              ) : null}
            </dl>

            <div className="space-y-2">
              <Label htmlFor="reclassify">{t("reclassify")}</Label>
              <Select
                value={e.category ?? undefined}
                onValueChange={(v) => reclassify.mutate(v as Category)}
                disabled={reclassify.isPending}
              >
                <SelectTrigger id="reclassify" className="w-full sm:w-64">
                  <SelectValue placeholder={tcat("unclassified")} />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => (
                    <SelectItem key={c} value={c}>
                      <CategoryBadge category={c} />
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {evidence.length ? (
              <section aria-labelledby="evidence" className="space-y-2">
                <h3 id="evidence" className="text-sm font-semibold">
                  {t("evidence")}
                </h3>
                <ul className="space-y-2">
                  {evidence.map((r, i) => (
                    <li
                      key={i}
                      className="rounded-md border-l-2 border-signal bg-muted/50 px-3 py-2 text-sm"
                    >
                      <span className="font-medium">{tl((r.layer as Layer) ?? "heuristic")}</span>
                      <span className="text-muted-foreground"> · {String(r.rule)}</span>
                      {r.excerpt ? (
                        <q className="mt-1 block break-words text-muted-foreground">
                          {String(r.excerpt)}
                        </q>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            <div className="flex flex-wrap gap-2 border-t pt-4">
              {e.injection_flag ? (
                <Button
                  variant="outline"
                  onClick={() => clearFlag.mutate()}
                  disabled={clearFlag.isPending}
                >
                  <ShieldCheck aria-hidden className="size-4" />
                  {t("clearFlag")}
                </Button>
              ) : null}
              <Button variant="ghost" asChild>
                <a href={e.gmail_url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink aria-hidden className="size-4" />
                  {tc("openInGmail")}
                </a>
              </Button>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
