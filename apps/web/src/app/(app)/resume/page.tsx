"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RotateCw, ShieldAlert, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { IconButton } from "@/components/icon-button";
import { ErrorState, PageHeader } from "@/components/page-header";
import { AtsReportView, JobAdForm } from "@/components/resume/ats-report";
import { ProfileCompare } from "@/components/resume/profile-compare";
import { ProfileEditor } from "@/components/resume/profile-editor";
import { UploadButton, UploadZone, useUploadResume } from "@/components/resume/upload-zone";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, unwrap } from "@/lib/api/client";
import { followTask } from "@/lib/api/task-stream";
import { dateTime, relativeTime } from "@/lib/format";
import { EMPTY_PROFILE, type Breakdown, type Upload, type Warning } from "@/lib/resume";
import { cn } from "@/lib/utils";

const BUSY = new Set(["uploaded", "processing"]);
const PARSE_ERRORS = [
  "no_text",
  "llm_unavailable",
  "parse_failed",
  "too_long",
  "extract_timeout",
  "active_content",
  "encrypted",
  "corrupt",
  "too_many_pages",
] as const;

type Active = { taskId: string; progress: number; message: string | null };

/** Worker progress messages (English, stable) -> translated step labels. */
const STEPS: Record<string, string> = {
  "CV received": "received",
  "extracting text": "extract",
  "scanning for prompt injection": "scan",
  "reading the CV (LLM)": "parse",
  "building the ATS report": "ats",
  "ATS analysis": "ats",
};

function StatusLabel({ status }: { status: string }) {
  const t = useTranslations("resume.file.status");
  const tone =
    status === "parsed" ? "text-success" : status === "failed" ? "text-destructive" : "text-signal";
  const known = ["uploaded", "processing", "parsed", "failed"].includes(status);
  return (
    <span className={cn("text-sm whitespace-nowrap", tone)}>
      {known ? t(status as "parsed") : status}
    </span>
  );
}

function Progress({ active, busy }: { active: Active | null; busy: boolean }) {
  const t = useTranslations("resume.progress");
  if (!active && !busy) return null;
  const step = active?.message ? STEPS[active.message] : undefined;
  const pct = Math.round((active?.progress ?? 0) * 100);
  return (
    <div role="status" className="rounded-lg border p-4">
      <div className="mb-2 flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium">{t("title")}</span>
        <span className="text-muted-foreground">{step ? t(`steps.${step}`) : t("waiting")}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full bg-primary transition-[width] duration-300",
            !pct && "w-1/12 animate-pulse",
          )}
          style={pct ? { width: `${pct}%` } : undefined}
        />
      </div>
    </div>
  );
}

export default function ResumePage() {
  const t = useTranslations("resume");
  const tn = useTranslations("nav");
  const tc = useTranslations("common");
  const locale = useLocale();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState("report");
  const [active, setActive] = useState<Active | null>(null);
  const [reportId, setReportId] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [manual, setManual] = useState(false);

  const uploads = useQuery({
    queryKey: ["resume", "uploads"],
    queryFn: () => unwrap(api.GET("/api/v1/resume/uploads")),
    refetchInterval: (q) => (q.state.data?.some((u) => BUSY.has(u.status)) ? 3000 : false),
  });
  const master = useQuery({
    queryKey: ["resume", "master"],
    queryFn: () => unwrap(api.GET("/api/v1/resume/master")),
  });
  const currentId = selected ?? uploads.data?.[0]?.id ?? null;
  const current: Upload | undefined = uploads.data?.find((u) => u.id === currentId);
  const busy = current ? BUSY.has(current.status) : false;

  const detail = useQuery({
    queryKey: ["resume", "upload", currentId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/resume/uploads/{resume_id}", {
          params: { path: { resume_id: currentId! } },
        }),
      ),
    enabled: currentId !== null,
    refetchInterval: busy ? 3000 : false,
  });
  const reports = useQuery({
    queryKey: ["resume", "reports", currentId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/ats/reports", { params: { query: { resume_id: currentId! } } })),
    enabled: currentId !== null,
  });

  useEffect(() => {
    setReportId(null);
    setManual(false);
  }, [currentId]);

  const follow = (taskId: string) => {
    setActive({ taskId, progress: 0, message: null });
    followTask(
      taskId,
      (event) => {
        setActive({ taskId, progress: event.progress ?? 0, message: event.message ?? null });
        if (["succeeded", "failed", "cancelled"].includes(event.status)) {
          setActive(null);
          setReportId(null);
          void queryClient.invalidateQueries({ queryKey: ["resume"] });
        }
      },
      () => setActive(null),
    );
  };

  const upload = useUploadResume((resumeId, taskId) => {
    setSelected(resumeId);
    setTab("report");
    follow(taskId);
  });

  const reparse = useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.POST("/api/v1/resume/uploads/{resume_id}/reparse", {
          params: { path: { resume_id: id }, query: { locale: locale as "fr" | "en" } },
        }),
      ),
    onSuccess: async (r) => {
      follow(r.task_id);
      await queryClient.invalidateQueries({ queryKey: ["resume"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.DELETE("/api/v1/resume/uploads/{resume_id}", { params: { path: { resume_id: id } } }),
      ),
    onSuccess: async () => {
      toast.success(t("file.deleted"));
      setConfirmDelete(false);
      setSelected(null);
      await queryClient.invalidateQueries({ queryKey: ["resume"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const headerActions =
    uploads.data?.length || master.data ? (
      <UploadButton onFile={(f) => upload.mutate(f)} pending={upload.isPending} variant="outline" />
    ) : null;

  if (uploads.isError || master.isError) {
    return (
      <>
        <PageHeader title={tn("resume")} description={t("description")} />
        <ErrorState
          message={tc("loadError")}
          retryLabel={tc("retry")}
          onRetry={() => {
            void uploads.refetch();
            void master.refetch();
          }}
        />
      </>
    );
  }
  if (uploads.isPending || master.isPending) {
    return (
      <>
        <PageHeader title={tn("resume")} description={t("description")} />
        <div className="space-y-4">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </>
    );
  }

  const m = master.data;
  if (!uploads.data.length && !m) {
    return (
      <>
        <PageHeader title={tn("resume")} description={t("description")} />
        <UploadZone onFile={(f) => upload.mutate(f)} pending={upload.isPending} />
      </>
    );
  }

  const d = detail.data;
  const reportList = reports.data ?? [];
  const report = reportList.find((r) => r.id === reportId) ?? reportList[0];
  const canCompare = Boolean(m && d?.parsed);
  const merged = Boolean(m && d && m.sources.includes(d.id));
  const parseError =
    d?.error && (PARSE_ERRORS as readonly string[]).includes(d.error) ? d.error : null;

  return (
    <>
      <PageHeader title={tn("resume")} description={t("description")} actions={headerActions} />

      {current ? (
        <div className="mb-6 flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <label htmlFor="doc-select" className="sr-only">
              {t("file.label")}
            </label>
            <Select value={String(current.id)} onValueChange={(v) => setSelected(Number(v))}>
              <SelectTrigger id="doc-select" className="w-full max-w-md">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {uploads.data.map((u) => (
                  <SelectItem key={u.id} value={String(u.id)}>
                    {u.original_filename ?? `#${u.id}`}
                    {u.latest_score !== null && u.latest_score !== undefined
                      ? ` · ${u.latest_score}/100`
                      : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="mt-1.5 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
              <StatusLabel status={current.status} />
              <span aria-hidden>·</span>
              <span>
                {t("file.uploadedAt", { when: relativeTime(current.uploaded_at, locale) })}
              </span>
              {current.page_count ? (
                <>
                  <span aria-hidden>·</span>
                  <span>{t("file.pages", { count: current.page_count })}</span>
                </>
              ) : null}
            </p>
          </div>
          <div role="group" aria-label={t("file.actions")} className="flex items-center gap-1">
            <IconButton label={t("file.download")} asChild>
              <a href={`/api/v1/resume/uploads/${current.id}/file`} download>
                <Download aria-hidden />
              </a>
            </IconButton>
            <IconButton
              label={t("file.reanalyze")}
              disabled={busy || reparse.isPending || active !== null}
              onClick={() => reparse.mutate(current.id)}
            >
              <RotateCw aria-hidden />
            </IconButton>
            <IconButton label={t("file.delete")} onClick={() => setConfirmDelete(true)}>
              <Trash2 aria-hidden />
            </IconButton>
          </div>
        </div>
      ) : null}

      <div className="space-y-6">
        <Progress active={active} busy={busy} />

        {d?.injection_flag ? (
          <div role="alert" className="rounded-lg border border-signal/40 bg-signal/5 p-4 text-sm">
            <p className="flex items-center gap-2 font-medium">
              <ShieldAlert aria-hidden className="size-4 text-signal" />
              {t("injection.title")}
            </p>
            <p className="mt-1 max-w-[70ch] text-muted-foreground">{t("injection.body")}</p>
            {d.hidden_excerpts.length ? (
              <details className="mt-2">
                <summary className="cursor-pointer">{t("injection.excerpts")}</summary>
                <ul className="mt-2 space-y-1 font-mono text-xs text-muted-foreground">
                  {d.hidden_excerpts.map((h, i) => (
                    <li key={i} className="break-words">
                      {h}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ) : null}

        {parseError && !busy ? (
          <p className="rounded-lg border border-destructive/40 p-4 text-sm">
            {t(`parseErrors.${parseError}`)}
          </p>
        ) : null}

        {canCompare && !merged && tab !== "compare" ? (
          <div className="flex flex-col gap-2 rounded-lg border border-primary/30 bg-accent p-3 text-sm sm:flex-row sm:items-center sm:justify-between">
            <p>{t("compare.banner")}</p>
            <Button size="sm" onClick={() => setTab("compare")}>
              {t("compare.open")}
            </Button>
          </div>
        ) : null}

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            {current ? <TabsTrigger value="report">{t("tabs.report")}</TabsTrigger> : null}
            <TabsTrigger value="profile">{t("tabs.profile")}</TabsTrigger>
            {canCompare ? <TabsTrigger value="compare">{t("tabs.compare")}</TabsTrigger> : null}
          </TabsList>

          {current ? (
            <TabsContent value="report" className="mt-6 space-y-8">
              {reportList.length > 1 ? (
                <div className="max-w-md space-y-1.5">
                  <label htmlFor="report-select" className="text-sm text-muted-foreground">
                    {t("report.history")}
                  </label>
                  <Select value={String(report?.id)} onValueChange={(v) => setReportId(Number(v))}>
                    <SelectTrigger id="report-select" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {reportList.map((r) => {
                        const target = (r.breakdown as unknown as Breakdown).target;
                        const when = dateTime(r.generated_at, locale);
                        return (
                          <SelectItem key={r.id} value={String(r.id)}>
                            {target
                              ? t("report.withJob", {
                                  when,
                                  score: r.score,
                                  title: target.title || t("report.pastedJob"),
                                })
                              : t("report.historyItem", { when, score: r.score })}
                          </SelectItem>
                        );
                      })}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
              {report ? (
                <AtsReportView report={report} onStarted={follow} />
              ) : reports.isPending || busy ? (
                <Skeleton className="h-48 w-full" />
              ) : (
                <p className="text-sm text-muted-foreground">{t("report.empty")}</p>
              )}
              {d &&
              d.status !== "uploaded" &&
              d.status !== "processing" &&
              current.status !== "failed" ? (
                <JobAdForm resumeId={d.id} onStarted={follow} />
              ) : null}
            </TabsContent>
          ) : null}

          <TabsContent value="profile" className="mt-6">
            {m ? (
              <ProfileEditor initial={m.profile} mode="master" />
            ) : d?.parsed ? (
              <ProfileEditor
                initial={d.parsed}
                warnings={d.warnings as unknown as Warning[]}
                mode="draft"
                sourceResumeId={d.id}
              />
            ) : manual ? (
              <ProfileEditor initial={EMPTY_PROFILE} mode="draft" sourceResumeId={d?.id} />
            ) : (
              <div className="space-y-3 rounded-lg border border-dashed p-6 text-sm">
                <p>{busy ? t("progress.waiting") : t("profile.noParse")}</p>
                {!busy ? (
                  <Button variant="outline" onClick={() => setManual(true)}>
                    {t("profile.startEmpty")}
                  </Button>
                ) : null}
              </div>
            )}
          </TabsContent>

          {canCompare && m && d?.parsed ? (
            <TabsContent value="compare" className="mt-6">
              <ProfileCompare
                master={m.profile}
                incoming={d.parsed}
                resumeId={d.id}
                alreadyMerged={merged}
                onApplied={() => setTab("profile")}
              />
            </TabsContent>
          ) : null}
        </Tabs>
      </div>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t("file.deleteTitle", { name: current?.original_filename ?? "" })}
            </DialogTitle>
            <DialogDescription>{t("file.deleteBody")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending || !current}
              onClick={() => current && remove.mutate(current.id)}
            >
              {t("file.deleteConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
