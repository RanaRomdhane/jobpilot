"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ExternalLink, Mail, ShieldAlert, Sparkles } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { ScorePill } from "@/components/jobs/job-card";
import { JobDocuments } from "@/components/documents/job-documents";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api, unwrap } from "@/lib/api/client";
import { dateTime } from "@/lib/format";
import { safeHref } from "@/lib/jobs";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function Bar({ label, value }: { label: string; value: number | null | undefined }) {
  if (value === null || value === undefined) return null;
  const pct = Math.round(value <= 1 ? value * 100 : value);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_2.5rem] items-center gap-3 text-sm">
      <div>
        <p className="mb-1 text-muted-foreground">{label}</p>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
          <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <span className="self-end text-right tabular-nums">{pct}</span>
    </div>
  );
}

export function JobDrawer({
  jobId,
  onClose,
  onTask,
}: {
  jobId: number | null;
  onClose: () => void;
  onTask: (taskId: string) => void;
}) {
  const t = useTranslations("jobs");
  const td = useTranslations("jobs.drawer");
  const locale = useLocale() as "fr" | "en";
  const job = useQuery({
    queryKey: ["jobs", "detail", jobId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId! } } })),
    enabled: jobId !== null,
  });
  const rerank = useMutation({
    mutationFn: (id: number) =>
      unwrap(api.POST("/api/v1/jobs/rerank", { body: { job_ids: [id], locale } })),
    onSuccess: (r) => {
      toast.success(td("rerankStarted"));
      onTask(r.task_id);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const j = job.data;
  const m = j?.match;
  const applyHref = safeHref(j?.apply_url) ?? safeHref(j?.url);
  const unknown = td("unknown");
  const salary =
    j && (j.salary_min || j.salary_max)
      ? t("card.salary", {
          min: j.salary_min ? Number(j.salary_min).toLocaleString(locale) : "…",
          max: j.salary_max ? Number(j.salary_max).toLocaleString(locale) : "…",
          currency: j.salary_currency ?? "",
        })
      : unknown;

  return (
    <Sheet open={jobId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        {!j ? (
          <div className="space-y-3 p-4" aria-label={td("loading")}>
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : (
          <>
            <SheetHeader>
              <SheetTitle className="pr-6">{j.title}</SheetTitle>
              <SheetDescription>
                {[j.company_name, j.location_raw].filter(Boolean).join(" · ")}
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-6 px-4 pb-6">
              {j.injection_flag ? (
                <div
                  role="alert"
                  className="rounded-lg border border-signal/40 bg-signal/5 p-3 text-sm"
                >
                  <p className="flex items-center gap-2 font-medium">
                    <ShieldAlert aria-hidden className="size-4 text-signal" />
                    {td("suspiciousTitle")}
                  </p>
                  <p className="mt-1 text-muted-foreground">{td("suspiciousBody")}</p>
                </div>
              ) : null}

              <section aria-labelledby="job-score" className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <h3 id="job-score" className="text-sm font-semibold">
                    {td("score")}
                  </h3>
                  <ScorePill score={m?.score} reviewed={Boolean(m?.llm_score)} />
                </div>
                {m ? (
                  <div className="grid gap-3">
                    <Bar label={td("vector")} value={m.vector_score} />
                    <Bar label={td("keywords")} value={m.keyword_score} />
                    <Bar label={td("llm")} value={m.llm_score} />
                  </div>
                ) : null}
                {m?.rationale ? (
                  <div>
                    <p className="text-xs text-muted-foreground">{td("rationale")}</p>
                    <p className="mt-0.5 text-sm">{m.rationale}</p>
                  </div>
                ) : null}
                {m?.matched_keywords.length || m?.missing_keywords.length ? (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <p className="mb-1.5 text-xs text-muted-foreground">{t("card.matched")}</p>
                      <ul className="flex flex-wrap gap-1.5 text-xs">
                        {m.matched_keywords.map((k) => (
                          <li key={k} className="rounded border border-success/40 px-1.5 py-0.5">
                            {k}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <p className="mb-1.5 text-xs text-muted-foreground">{t("card.missing")}</p>
                      <ul className="flex flex-wrap gap-1.5 text-xs">
                        {m.missing_keywords.map((k) => (
                          <li
                            key={k}
                            className="rounded border border-dashed border-signal/60 px-1.5 py-0.5"
                          >
                            {k}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                ) : null}
                {m && !m.llm_score && !j.injection_flag ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={rerank.isPending}
                    onClick={() => rerank.mutate(j.id)}
                  >
                    <Sparkles aria-hidden />
                    {td("rerank")}
                  </Button>
                ) : null}
              </section>

              <section aria-labelledby="job-details">
                <h3 id="job-details" className="mb-1 text-sm font-semibold">
                  {td("details")}
                </h3>
                <dl className="divide-y">
                  <Row label={td("location")}>
                    {[j.location_raw, j.region ? t(`regions.${j.region}`) : null]
                      .filter(Boolean)
                      .join(" · ") || unknown}
                  </Row>
                  <Row label={td("workMode")}>
                    {j.work_mode ? t(`workModes.${j.work_mode}`) : unknown}
                  </Row>
                  <Row label={td("contract")}>
                    {j.contract_type ? t(`contracts.${j.contract_type as "permanent"}`) : unknown}
                  </Row>
                  <Row label={td("seniority")}>
                    {j.seniority ? t(`seniority.${j.seniority as "junior"}`) : unknown}
                  </Row>
                  <Row label={td("salary")}>{salary}</Row>
                  <Row label={td("language")}>
                    {j.language ? t(`languages.${j.language as "fr"}`) : unknown}
                  </Row>
                  <Row label={td("posted")}>{dateTime(j.posted_at ?? j.scraped_at, locale)}</Row>
                  <Row label={td("source")}>
                    <span className="block">{j.source_name}</span>
                    {j.also_on.map((a) => {
                      const href = safeHref(String(a.url ?? ""));
                      return href ? (
                        <a
                          key={String(a.source)}
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer nofollow"
                          className="block text-primary hover:underline"
                        >
                          {String(a.source)}
                        </a>
                      ) : (
                        <span key={String(a.source)} className="block">
                          {String(a.source)}
                        </span>
                      );
                    })}
                    {j.attribution ? (
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {j.attribution}
                      </span>
                    ) : null}
                  </Row>
                </dl>
              </section>

              <div className="flex flex-wrap gap-2">
                {applyHref ? (
                  <Button asChild>
                    {/* untrusted link: new tab, no referrer, no opener */}
                    <a href={applyHref} target="_blank" rel="noopener noreferrer nofollow">
                      <ExternalLink aria-hidden />
                      {td("apply", { source: j.source_name })}
                    </a>
                  </Button>
                ) : null}
              </div>
              {j.apply_email ? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Mail aria-hidden className="size-4" />
                  {td("applyEmail", { email: j.apply_email })}
                </p>
              ) : null}

              <JobDocuments jobId={j.id} flagged={j.injection_flag} />

              <section aria-labelledby="job-description">
                <h3 id="job-description" className="mb-2 text-sm font-semibold">
                  {td("description")}
                </h3>
                {/* plain text (HTML stripped server-side), never rendered as HTML */}
                <p className="text-sm leading-relaxed whitespace-pre-line">{j.description}</p>
              </section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
