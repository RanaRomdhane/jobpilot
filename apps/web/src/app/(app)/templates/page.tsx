"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  FileUp,
  Loader2,
  RefreshCw,
  TriangleAlert,
  UserRoundCog,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ErrorState, PageHeader } from "@/components/page-header";
import { ProfileCompare } from "@/components/resume/profile-compare";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api, unwrap } from "@/lib/api/client";
import { followTask } from "@/lib/api/task-stream";
import {
  type ChunkCheck,
  type Headings,
  type Template,
  type TemplateKind,
  pct,
  templateState,
} from "@/lib/templates";

const ACCEPT = ".tex,.cls,.sty,.bib,.bst,.zip,.png,.jpg,.jpeg,.pdf,.ttf,.otf";

export default function TemplatesPage() {
  const t = useTranslations("templates");
  const tc = useTranslations("common");
  const list = useQuery({
    queryKey: ["templates"],
    queryFn: () => unwrap(api.GET("/api/v1/templates")),
  });
  const [busy, setBusy] = useState<Partial<Record<TemplateKind, boolean>>>({});

  if (list.isError)
    return (
      <ErrorState
        message={tc("loadError")}
        retryLabel={tc("retry")}
        onRetry={() => void list.refetch()}
      />
    );

  const latest = (kind: TemplateKind): Template | undefined =>
    (list.data ?? []).find((x) => x.kind === kind && x.is_default) ??
    (list.data ?? []).find((x) => x.kind === kind);

  return (
    <div className="mx-auto w-full max-w-5xl">
      <PageHeader title={t("title")} description={t("description")} />
      {list.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <Tabs defaultValue="cv">
          <TabsList>
            {(["cv", "cover_letter"] as const).map((kind) => (
              <TabsTrigger key={kind} value={kind} className="gap-2">
                {t(`kinds.${kind}`)}
                <StateBadge state={templateState(latest(kind), Boolean(busy[kind]))} />
              </TabsTrigger>
            ))}
          </TabsList>
          {(["cv", "cover_letter"] as const).map((kind) => {
            const tpl = latest(kind);
            return (
              <TabsContent key={kind} value={kind} className="mt-6 space-y-8">
                <UploadPanel
                  kind={kind}
                  current={tpl}
                  busy={Boolean(busy[kind])}
                  setBusy={(b) => setBusy((s) => ({ ...s, [kind]: b }))}
                />
                {tpl && tpl.converted && !busy[kind] ? (
                  <TemplateReview key={tpl.id} id={tpl.id} kind={kind} />
                ) : null}
              </TabsContent>
            );
          })}
        </Tabs>
      )}
    </div>
  );
}

function StateBadge({ state }: { state: ReturnType<typeof templateState> }) {
  const t = useTranslations("templates.state");
  const variant = state === "ready" ? "default" : state === "failed" ? "destructive" : "secondary";
  return <Badge variant={variant}>{t(state)}</Badge>;
}

function useFollow(onDone: (ok: boolean, error: string | null) => void) {
  const [progress, setProgress] = useState<number | null>(null);
  const stop = useRef<(() => void) | null>(null);
  useEffect(() => () => stop.current?.(), []);
  return {
    progress,
    follow(taskId: string) {
      setProgress(0);
      stop.current = followTask(
        taskId,
        (e) => {
          setProgress(e.progress ?? 0);
          if (e.status === "succeeded" || e.status === "failed") {
            setProgress(null);
            onDone(e.status === "succeeded", e.error ?? null);
          }
        },
        () => setProgress(null),
      );
    },
  };
}

function UploadPanel({
  kind,
  current,
  busy,
  setBusy,
}: {
  kind: TemplateKind;
  current: Template | undefined;
  busy: boolean;
  setBusy: (b: boolean) => void;
}) {
  const t = useTranslations("templates.upload");
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const tracker = useFollow((ok, error) => {
    setBusy(false);
    void queryClient.invalidateQueries({ queryKey: ["templates"] });
    if (ok) toast.success(t("converted"));
    else toast.error(t("convertFailed", { error: error ?? "" }));
  });

  const upload = useMutation({
    mutationFn: (files: File[]) =>
      unwrap(
        api.POST("/api/v1/templates", {
          body: { kind, files: files as unknown as string[] },
          bodySerializer: () => {
            const data = new FormData();
            data.append("kind", kind);
            for (const f of files) data.append("files", f);
            return data;
          },
        }),
      ),
    onSuccess: async (r) => {
      setBusy(true);
      await queryClient.invalidateQueries({ queryKey: ["templates"] });
      tracker.follow(r.task_id);
    },
    onError: (e: Error) =>
      toast.error(e instanceof ApiError ? t("rejected", { reason: e.detail }) : t("generic")),
  });

  return (
    <section aria-labelledby={`upload-${kind}`} className="space-y-3">
      <h2 id={`upload-${kind}`} className="text-base font-semibold">
        {current ? t("replaceTitle") : t("title")}
      </h2>
      <p className="max-w-[65ch] text-sm text-muted-foreground">{t(`hint.${kind}`)}</p>
      {current ? (
        <p className="text-sm">
          {t("current", { name: current.name, engine: current.engine ?? "pdflatex" })}{" "}
          <span className="text-muted-foreground">{current.files.join(", ")}</span>
        </p>
      ) : null}
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        className="sr-only"
        aria-label={t("choose")}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) upload.mutate(files);
          e.target.value = "";
        }}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={() => input.current?.click()} disabled={busy || upload.isPending}>
          {busy ? <Loader2 aria-hidden className="animate-spin" /> : <FileUp aria-hidden />}
          {t("choose")}
        </Button>
        {busy ? (
          <span role="status" aria-live="polite" className="text-sm text-muted-foreground">
            {t("converting", { pct: Math.round((tracker.progress ?? 0) * 100) })}
          </span>
        ) : null}
      </div>
    </section>
  );
}

function TemplateReview({ id, kind }: { id: number; kind: TemplateKind }) {
  const t = useTranslations("templates.review");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["templates", id],
    queryFn: () =>
      unwrap(api.GET("/api/v1/templates/{template_id}", { params: { path: { template_id: id } } })),
  });
  const [headings, setHeadings] = useState<Headings>({});
  const [budget, setBudget] = useState(1);
  const [removeBreaks, setRemoveBreaks] = useState(false);
  const [source, setSource] = useState("");
  const [previewTs, setPreviewTs] = useState(() => Date.now());
  const [rebuildOpen, setRebuildOpen] = useState(false);

  useEffect(() => {
    if (!detail.data) return;
    const m = detail.data.mapping as { headings?: Headings; remove_page_breaks?: boolean };
    setHeadings(m.headings ?? {});
    setBudget(detail.data.page_budget);
    setRemoveBreaks(
      Boolean(m.remove_page_breaks) || detail.data.warnings.includes("forced_page_break"),
    );
    setSource(detail.data.source ?? "");
  }, [detail.data]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["templates"] });
    setPreviewTs(Date.now());
  };
  const preview = useFollow((ok, error) => {
    refresh();
    if (!ok) toast.error(t("previewFailed", { error: error ?? "" }));
  });
  const runPreview = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/templates/{template_id}/preview", {
          params: { path: { template_id: id } },
          body: { use: "master" },
        }),
      ),
    onSuccess: (r) => preview.follow(r.task_id),
  });
  const saveSource = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/templates/{template_id}/source", {
          params: { path: { template_id: id } },
          body: { source },
        }),
      ),
    onSuccess: () => {
      toast.success(t("sourceSaved"));
      refresh();
    },
    onError: (e: Error) => toast.error(e instanceof ApiError ? e.detail : t("generic")),
  });
  const converting = useFollow((ok, error) => {
    refresh();
    void queryClient.invalidateQueries({ queryKey: ["templates", id] });
    if (ok) toast.success(t("reconverted"));
    else toast.error(t("previewFailed", { error: error ?? "" }));
  });
  const reconvert = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/templates/{template_id}/convert", {
          params: { path: { template_id: id } },
        }),
      ),
    onSuccess: (r) => converting.follow(r.task_id),
  });
  const confirm = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/templates/{template_id}/confirm", {
          params: { path: { template_id: id } },
          body: {
            page_budget: budget,
            remove_page_breaks: removeBreaks,
            headings,
            make_default: true,
          },
        }),
      ),
    onSuccess: () => {
      toast.success(t("confirmed"));
      refresh();
    },
  });

  if (detail.isPending) return <Skeleton className="h-96 w-full" />;
  if (detail.isError || !detail.data)
    return (
      <ErrorState
        message={tc("loadError")}
        retryLabel={tc("retry")}
        onRetry={() => void detail.refetch()}
      />
    );
  const d = detail.data;
  const m = d.mapping as {
    chunks?: ChunkCheck[];
    check?: { ratio: number; ok: boolean; missing: string[]; missing_numbers: string[] } | null;
    preview?: { ok: boolean; error: string | null; pages: number | null } | null;
  };
  const chunks = m.chunks ?? [];
  const failed = chunks.filter((c) => !c.ok);
  // the preamble alone (metadata) is not a usable template: nothing would be tailored
  const usable = chunks.some((c) => c.ok && c.kind !== "preamble");
  const hasExtracted = Boolean((d.mapping as { has_extracted?: boolean }).has_extracted);
  const chunkError = (error: string | null) =>
    error === "llm_budget" || error === "llm_error" ? t(`errors.${error}`) : (error ?? "");

  return (
    <div className="space-y-8">
      <section aria-labelledby={`check-${id}`} className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id={`check-${id}`} className="text-base font-semibold">
            {t("checkTitle")}
          </h2>
          {failed.length === 0 ? (
            <span className="text-sm text-muted-foreground">
              {t("match", { pct: pct(m.check?.ratio) })}
            </span>
          ) : null}
        </div>
        <p className="max-w-[65ch] text-sm text-muted-foreground">{t("checkHint")}</p>
        <ul className="divide-y rounded-lg border">
          {chunks.map((c, i) => (
            <li key={i} className="flex items-start gap-3 px-3 py-2 text-sm">
              {c.ok ? (
                <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-primary" />
              ) : (
                <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
              )}
              <div className="min-w-0 flex-1">
                <p className="font-medium">{c.title || t(`chunk.${c.kind}` as "chunk.preamble")}</p>
                {c.ok ? null : (
                  <p className="text-xs break-words text-muted-foreground">
                    {t("chunkFailed")} {chunkError(c.error)}
                  </p>
                )}
              </div>
              <span className="shrink-0 text-xs text-muted-foreground">{pct(c.ratio)}</span>
            </li>
          ))}
        </ul>
        {failed.length ? (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
            <p className="min-w-0 flex-1 text-sm">{t("failedHint", { n: failed.length })}</p>
            <Button
              variant="outline"
              onClick={() => reconvert.mutate()}
              disabled={converting.progress !== null || reconvert.isPending}
            >
              {converting.progress !== null ? (
                <Loader2 aria-hidden className="animate-spin" />
              ) : (
                <RefreshCw aria-hidden />
              )}
              {converting.progress !== null
                ? t("reconverting", { pct: Math.round(converting.progress * 100) })
                : t("reconvert")}
            </Button>
          </div>
        ) : null}
      </section>

      {Object.keys(headings).length ? (
        <section aria-labelledby={`headings-${id}`} className="space-y-3">
          <h2 id={`headings-${id}`} className="text-base font-semibold">
            {t("headingsTitle")}
          </h2>
          <p className="max-w-[65ch] text-sm text-muted-foreground">{t("headingsHint")}</p>
          <div className="grid gap-3">
            {Object.entries(headings).map(([key, h]) => (
              <div key={key} className="grid gap-2 sm:grid-cols-2">
                {(["en", "fr"] as const).map((lang) => (
                  <div key={lang} className="space-y-1">
                    <Label htmlFor={`h-${id}-${key}-${lang}`}>
                      {t(`lang.${lang}`)} · {key}
                    </Label>
                    <Input
                      id={`h-${id}-${key}-${lang}`}
                      value={h[lang]}
                      maxLength={120}
                      onChange={(e) =>
                        setHeadings((s) => ({ ...s, [key]: { ...s[key], [lang]: e.target.value } }))
                      }
                    />
                  </div>
                ))}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section aria-labelledby={`preview-${id}`} className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id={`preview-${id}`} className="text-base font-semibold">
            {t("previewTitle")}
          </h2>
          <Button
            variant="outline"
            onClick={() => runPreview.mutate()}
            disabled={preview.progress !== null || runPreview.isPending}
          >
            {preview.progress !== null ? (
              <Loader2 aria-hidden className="animate-spin" />
            ) : (
              <RefreshCw aria-hidden />
            )}
            {t("previewMaster")}
          </Button>
        </div>
        {m.preview && !m.preview.ok ? (
          <p className="text-sm text-destructive">
            {t("previewFailed", { error: m.preview.error ?? "" })}
          </p>
        ) : null}
        {m.preview?.ok ? (
          <>
            <p className="text-sm text-muted-foreground">
              {t("pages", { n: m.preview.pages ?? 0 })}
            </p>
            <iframe
              title={t("previewTitle")}
              src={`/api/v1/templates/${id}/preview.pdf?ts=${previewTs}`}
              className="h-[70vh] w-full rounded-lg border bg-white"
            />
          </>
        ) : null}
      </section>

      <section aria-labelledby={`options-${id}`} className="space-y-4">
        <h2 id={`options-${id}`} className="text-base font-semibold">
          {t("optionsTitle")}
        </h2>
        {kind === "cv" ? (
          <div className="space-y-1">
            <Label htmlFor={`budget-${id}`}>{t("budget")}</Label>
            <Select value={String(budget)} onValueChange={(v) => setBudget(Number(v))}>
              <SelectTrigger id={`budget-${id}`} className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[1, 2].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {t("pages", { n })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}
        {d.warnings.includes("forced_page_break") ? (
          <div className="flex items-start gap-2">
            <Checkbox
              id={`breaks-${id}`}
              checked={removeBreaks}
              onCheckedChange={(v) => setRemoveBreaks(v === true)}
            />
            <Label htmlFor={`breaks-${id}`} className="leading-snug font-normal">
              {t("removeBreaks")}
            </Label>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => confirm.mutate()} disabled={confirm.isPending || !usable}>
            <CheckCircle2 aria-hidden />
            {d.mapping_confirmed ? t("reconfirm") : t("confirm")}
          </Button>
          {kind === "cv" ? (
            <Button variant="outline" onClick={() => setRebuildOpen(true)} disabled={!hasExtracted}>
              <UserRoundCog aria-hidden />
              {t("rebuild")}
            </Button>
          ) : null}
        </div>
        {!usable ? (
          <p className="text-sm text-muted-foreground">{t("notUsable")}</p>
        ) : failed.length ? (
          <p className="text-sm text-muted-foreground">{t("partialWarning")}</p>
        ) : null}
        {d.mapping_confirmed ? (
          <p className="text-sm text-muted-foreground">{t("confirmedHint")}</p>
        ) : null}
      </section>

      <details className="rounded-lg border p-4">
        <summary className="cursor-pointer text-sm font-semibold">{t("sourceTitle")}</summary>
        <p className="mt-2 max-w-[65ch] text-sm text-muted-foreground">{t("sourceHint")}</p>
        <Label htmlFor={`source-${id}`} className="sr-only">
          {t("sourceTitle")}
        </Label>
        <Textarea
          id={`source-${id}`}
          value={source}
          onChange={(e) => setSource(e.target.value)}
          spellCheck={false}
          className="mt-3 h-96 font-mono text-xs"
        />
        <Button
          className="mt-3"
          variant="outline"
          onClick={() => saveSource.mutate()}
          disabled={saveSource.isPending || source === (d.source ?? "")}
        >
          {t("saveSource")}
        </Button>
      </details>

      {kind === "cv" ? (
        <RebuildDialog id={id} open={rebuildOpen} onOpenChange={setRebuildOpen} />
      ) : null}
    </div>
  );
}

function RebuildDialog({
  id,
  open,
  onOpenChange,
}: {
  id: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const t = useTranslations("templates.rebuild");
  const proposal = useQuery({
    enabled: open,
    queryKey: ["templates", id, "profile-proposal"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/templates/{template_id}/profile-proposal", {
          params: { path: { template_id: id } },
        }),
      ),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        {proposal.isPending ? <Skeleton className="h-64 w-full" /> : null}
        {proposal.isError ? <p className="text-sm text-destructive">{t("unavailable")}</p> : null}
        {proposal.data ? (
          proposal.data.current ? (
            <ProfileCompare
              master={proposal.data.current}
              incoming={proposal.data.proposed}
              resumeId={null}
              alreadyMerged={false}
              onApplied={() => onOpenChange(false)}
            />
          ) : (
            <p className="text-sm">{t("noMaster")}</p>
          )
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
