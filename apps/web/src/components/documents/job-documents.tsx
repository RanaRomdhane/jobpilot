"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Loader2, Send, ShieldAlert, Sparkles } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
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
import { ApiError, api, unwrap } from "@/lib/api/client";
import { followTask } from "@/lib/api/task-stream";
import { relativeTime } from "@/lib/format";
import { type DocumentSummary, latestByKind, pct } from "@/lib/templates";

const FACTORY_ERRORS = [
  "no_master",
  "template_not_ready",
  "job_flagged",
  "llm_unavailable",
  "job_not_found",
  "llm_budget",
] as const;

/** The tailored CV + cover letter for one job: generate, then open the PDFs. Never sends. */
export function JobDocuments({ jobId, flagged }: { jobId: number; flagged: boolean }) {
  const t = useTranslations("documents");
  const locale = useLocale() as "fr" | "en";
  const queryClient = useQueryClient();
  const [tone, setTone] = useState<"formal" | "modern">("formal");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [running, setRunning] = useState<{ progress: number; message: string | null } | null>(null);

  const docs = useQuery({
    queryKey: ["documents", "job", jobId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/documents/by-job/{job_id}", { params: { path: { job_id: jobId } } })),
  });

  const generate = useMutation({
    mutationFn: (confirmFlagged: boolean) =>
      unwrap(
        api.POST("/api/v1/documents/generate", {
          body: { job_id: jobId, locale, tone, confirm_flagged: confirmFlagged },
        }),
      ),
    onSuccess: (r) => {
      setRunning({ progress: 0, message: null });
      followTask(
        r.task_id,
        (event) => {
          setRunning({ progress: event.progress ?? 0, message: event.message ?? null });
          if (event.status === "succeeded" || event.status === "failed") {
            setRunning(null);
            void queryClient.invalidateQueries({ queryKey: ["documents", "job", jobId] });
            void queryClient.invalidateQueries({ queryKey: ["applications"] });
            if (event.status === "succeeded") toast.success(t("done"));
            else toast.error(errorText(event.error ?? ""));
          }
        },
        () => setRunning(null),
      );
    },
    onError: (e: Error) =>
      toast.error(e instanceof ApiError ? errorText(e.detail) : t("errors.generic")),
  });

  function errorText(raw: string): string {
    const code = FACTORY_ERRORS.find((c) => raw.startsWith(c));
    return code ? t(`errors.${code}`) : t("errors.generic");
  }

  // a task started elsewhere (another tab) finishes: refresh when the drawer regains focus
  useEffect(() => {
    const onFocus = () => void docs.refetch();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [docs]);

  const latest = latestByKind(docs.data?.documents ?? []);
  const has = Boolean(latest.cv || latest.cover_letter);

  return (
    <section aria-labelledby="job-documents" className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="job-documents" className="text-sm font-semibold">
          {t("title")}
        </h3>
        {docs.data?.status ? (
          <Badge variant="secondary">{t(`status.${docs.data.status}` as "status.draft")}</Badge>
        ) : null}
      </div>
      <p className="text-sm text-muted-foreground">{t("intro")}</p>

      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <label htmlFor={`tone-${jobId}`} className="text-xs font-medium text-muted-foreground">
            {t("tone")}
          </label>
          <Select value={tone} onValueChange={(v) => setTone(v as "formal" | "modern")}>
            <SelectTrigger id={`tone-${jobId}`} className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="formal">{t("tones.formal")}</SelectItem>
              <SelectItem value="modern">{t("tones.modern")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button
          disabled={running !== null || generate.isPending}
          onClick={() => (flagged ? setConfirmOpen(true) : generate.mutate(false))}
        >
          {running ? <Loader2 aria-hidden className="animate-spin" /> : <Sparkles aria-hidden />}
          {has ? t("regenerate") : t("generate")}
        </Button>
      </div>

      {running ? (
        <div role="status" aria-live="polite" className="space-y-1">
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary transition-[width] duration-300"
              style={{ width: `${Math.max(5, Math.round(running.progress * 100))}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground">{t("running")}</p>
        </div>
      ) : null}

      {has ? (
        <ul className="space-y-2">
          {(["cv", "cover_letter"] as const).map((kind) =>
            latest[kind] ? <DocumentRow key={kind} doc={latest[kind]} /> : null,
          )}
        </ul>
      ) : null}

      {has && docs.data?.application_id ? (
        <Button asChild variant="secondary" size="sm">
          <Link href={`/review?application=${docs.data.application_id}`}>
            <Send aria-hidden />
            {t("review")}
          </Link>
        </Button>
      ) : null}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldAlert aria-hidden className="size-5 text-destructive" />
              {t("flagged.title")}
            </DialogTitle>
            <DialogDescription>{t("flagged.body")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              {t("flagged.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmOpen(false);
                generate.mutate(true);
              }}
            >
              {t("flagged.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function DocumentRow({ doc }: { doc: DocumentSummary }) {
  const t = useTranslations("documents");
  const locale = useLocale();
  const ok = doc.compile_status === "success";
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/40 px-3 py-2">
      <div className="min-w-0 space-y-0.5">
        <p className="flex items-center gap-2 text-sm font-medium">
          <FileText aria-hidden className="size-4 shrink-0" />
          {t(`kinds.${doc.kind}` as "kinds.cv")}
          <span className="text-xs font-normal text-muted-foreground">
            v{doc.version} · {relativeTime(doc.created_at, locale)}
          </span>
        </p>
        <p className="text-xs text-muted-foreground">
          {ok
            ? [
                t("pages", { n: doc.page_count ?? 0 }),
                doc.language ? t(`languages.${doc.language}` as "languages.fr") : null,
                doc.kind === "cv" && doc.ats_after != null
                  ? t("ats", { before: pct(doc.ats_before), after: pct(doc.ats_after) })
                  : null,
                doc.findings ? t("findings", { n: doc.findings }) : null,
              ]
                .filter(Boolean)
                .join(" · ")
            : t("failed", { error: doc.error ?? "" })}
        </p>
      </div>
      <div className="flex gap-2">
        {ok ? (
          <Button asChild size="sm" variant="outline">
            <a href={`/api/v1/documents/${doc.id}/pdf`} target="_blank" rel="noopener noreferrer">
              {t("open")}
            </a>
          </Button>
        ) : (
          <Button asChild size="sm" variant="ghost">
            <Link href="/templates">{t("fixTemplate")}</Link>
          </Button>
        )}
      </div>
    </li>
  );
}
