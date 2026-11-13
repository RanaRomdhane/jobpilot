"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  FileText,
  Link2,
  Loader2,
  Send,
  ShieldAlert,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api, unwrap } from "@/lib/api/client";
import { followTask } from "@/lib/api/task-stream";
import {
  type EmailEdit,
  type Review,
  editOf,
  isCheck,
  overridable,
  parseCc,
  sameEdit,
  sendError,
} from "@/lib/review";

export function ReviewDetail({ applicationId }: { applicationId: number }) {
  const t = useTranslations("review");
  const review = useQuery({
    queryKey: ["outgoing", "review", applicationId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/outgoing/review/{application_id}", {
          params: { path: { application_id: applicationId } },
        }),
      ),
  });

  if (review.isPending) return <Skeleton className="h-[32rem]" />;
  if (review.isError || !review.data) {
    return (
      <Alert variant="destructive">
        <AlertTriangle aria-hidden />
        <AlertTitle>{t("loadError")}</AlertTitle>
      </Alert>
    );
  }
  return <ReviewBody data={review.data} />;
}

function ReviewBody({ data }: { data: Review }) {
  const t = useTranslations("review");
  const sent = data.email.status === "sent";
  const byLink = data.apply_channel === "link" && !data.job?.apply_email;
  return (
    <article aria-labelledby="review-title" className="min-w-0 space-y-6">
      <header className="space-y-1">
        <h2 id="review-title" className="text-lg font-semibold">
          {data.role_title}
        </h2>
        <p className="text-sm text-muted-foreground">
          {[
            data.job?.company,
            data.job?.match_score != null
              ? t("match", { score: Math.round(data.job.match_score) })
              : null,
            t("sentToday", { n: data.checks.sent_today, cap: data.checks.cap }),
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </header>

      {sent ? <SentNotice data={data} /> : <Checks data={data} />}

      <Documents data={data} />
      {byLink && !sent ? <ApplyByLink data={data} /> : null}
      <EmailForm data={data} />
      <Changes data={data} />
    </article>
  );
}

function SentNotice({ data }: { data: Review }) {
  const t = useTranslations("review");
  return (
    <Alert>
      <CheckCircle2 aria-hidden className="text-success" />
      <AlertTitle>{t("sent.title", { to: data.email.to_address ?? "" })}</AlertTitle>
      <AlertDescription>{t("sent.body")}</AlertDescription>
    </Alert>
  );
}

function Checks({ data }: { data: Review }) {
  const t = useTranslations("review");
  const tc = useTranslations("review.checks");
  const { blocking, warnings } = data.checks;
  const label = (code: string) => (isCheck(code) ? tc(code) : code);
  if (!blocking.length && !warnings.length) {
    return (
      <Alert>
        <CheckCircle2 aria-hidden className="text-success" />
        <AlertTitle>{t("ready")}</AlertTitle>
      </Alert>
    );
  }
  return (
    <div className="space-y-2">
      {blocking.length ? (
        <Alert variant="destructive">
          <ShieldAlert aria-hidden />
          <AlertTitle>{t("blocking")}</AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-0.5 pl-4">
              {blocking.map((c) => (
                <li key={c}>
                  {label(c)}
                  {c === "gmail_send_not_connected" ? (
                    <code className="ml-1 rounded bg-muted px-1 text-xs">make gmail-auth-send</code>
                  ) : null}
                  {c === "sending_disabled" ? (
                    <Link href="/settings" className="ml-1 underline">
                      {t("openSettings")}
                    </Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
      {warnings.length ? (
        <Alert>
          <AlertTriangle aria-hidden className="text-signal" />
          <AlertTitle>{t("warnings")}</AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-0.5 pl-4">
              {warnings.map((c) => (
                <li key={c}>{label(c)}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}

function Documents({ data }: { data: Review }) {
  const t = useTranslations("review");
  const td = useTranslations("documents");
  const docs = [
    { kind: "cv" as const, doc: data.cv },
    { kind: "cover_letter" as const, doc: data.cover_letter },
  ];
  return (
    <section aria-labelledby="docs-title" className="space-y-3">
      <h3 id="docs-title" className="text-sm font-semibold">
        {t("documents")}
      </h3>
      <div className="grid gap-4 xl:grid-cols-2">
        {docs.map(({ kind, doc }) => (
          <div key={kind} className="space-y-2 rounded-lg border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="flex items-center gap-2 text-sm font-medium">
                <FileText aria-hidden className="size-4" />
                {td(`kinds.${kind}`)}
                {doc ? <span className="text-xs text-muted-foreground">v{doc.version}</span> : null}
              </p>
              {doc ? (
                <div className="flex items-center gap-2">
                  {doc.findings ? (
                    <Badge variant="outline">{td("findings", { n: doc.findings })}</Badge>
                  ) : null}
                  <Button asChild size="sm" variant="outline">
                    <a
                      href={`/api/v1/documents/${doc.id}/pdf`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <ExternalLink aria-hidden />
                      {td("open")}
                    </a>
                  </Button>
                </div>
              ) : null}
            </div>
            {doc ? (
              <iframe
                title={td(`kinds.${kind}`)}
                src={`/api/v1/documents/${doc.id}/pdf#view=FitH`}
                className="hidden h-[28rem] w-full rounded border bg-white md:block"
              />
            ) : (
              <p className="text-sm text-muted-foreground">{t("noDocument")}</p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function ApplyByLink({ data }: { data: Review }) {
  const t = useTranslations("review");
  const queryClient = useQueryClient();
  const mark = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/outgoing/applications/{application_id}/applied-via-link", {
          params: { path: { application_id: data.application_id } },
        }),
      ),
    onSuccess: () => {
      toast.success(t("link.done"));
      void queryClient.invalidateQueries({ queryKey: ["outgoing"] });
    },
    onError: () => toast.error(t("link.error")),
  });
  return (
    <Alert>
      <Link2 aria-hidden />
      <AlertTitle>{t("link.title")}</AlertTitle>
      <AlertDescription className="space-y-2">
        <p>{t("link.body")}</p>
        <div className="flex flex-wrap gap-2">
          {data.apply_url ? (
            <Button asChild size="sm" variant="outline">
              <a href={data.apply_url} target="_blank" rel="noopener noreferrer nofollow">
                <ExternalLink aria-hidden />
                {t("link.open")}
              </a>
            </Button>
          ) : null}
          <Button size="sm" onClick={() => mark.mutate()} disabled={mark.isPending}>
            {t("link.mark")}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

function EmailForm({ data }: { data: Review }) {
  const t = useTranslations("review");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const saved = editOf(data.email);
  const [edit, setEdit] = useState<EmailEdit>(saved);
  const [ccText, setCcText] = useState((saved.cc ?? []).join(", "));
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [sending, setSending] = useState(false);
  useEffect(() => {
    setEdit(editOf(data.email));
    setCcText(data.email.cc.join(", "));
  }, [data.email]);

  const editable = data.email.status === "draft" || data.email.status === "failed";
  const current = { ...edit, cc: parseCc(ccText) };
  const dirty = !sameEdit(current, saved);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["outgoing"] });

  const save = useMutation({
    mutationFn: (body: EmailEdit) =>
      unwrap(
        api.PUT("/api/v1/outgoing/{email_id}", {
          params: { path: { email_id: data.email.id } },
          body,
        }),
      ),
    onSuccess: () => {
      toast.success(t("savedEmail"));
      void refresh();
    },
    onError: (e: Error) =>
      toast.error(
        e instanceof ApiError && isCheck(e.detail) ? t(`checks.${e.detail}`) : t("saveError"),
      ),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(current);
  }

  const { duplicate, flagged, others } = overridable(data.checks.blocking);
  const canApprove = editable && !dirty && !sending && others.length === 0;

  return (
    <section aria-labelledby="email-title" className="space-y-3">
      <h3 id="email-title" className="text-sm font-semibold">
        {t("email")}
      </h3>
      {data.email.status === "failed" && data.email.error ? (
        <Alert variant="destructive">
          <AlertTriangle aria-hidden />
          <AlertTitle>{t("failed")}</AlertTitle>
          <AlertDescription className="break-words">{data.email.error}</AlertDescription>
        </Alert>
      ) : null}
      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="to">{t("to")}</Label>
            <Input
              id="to"
              type="email"
              inputMode="email"
              autoComplete="off"
              value={edit.to_address ?? ""}
              onChange={(e) => setEdit({ ...edit, to_address: e.target.value })}
              disabled={!editable}
              aria-describedby="to-hint"
            />
            <p id="to-hint" className="text-xs text-muted-foreground">
              {data.job?.apply_email ? t("toFromPosting") : t("toManual")}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cc">{t("cc")}</Label>
            <Input
              id="cc"
              autoComplete="off"
              value={ccText}
              onChange={(e) => setCcText(e.target.value)}
              disabled={!editable}
              aria-describedby="cc-hint"
            />
            <p id="cc-hint" className="text-xs text-muted-foreground">
              {t("ccHint")}
            </p>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="subject">{t("subject")}</Label>
          <Input
            id="subject"
            value={edit.subject}
            maxLength={200}
            onChange={(e) => setEdit({ ...edit, subject: e.target.value })}
            disabled={!editable}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="body">{t("body")}</Label>
          <Textarea
            id="body"
            rows={12}
            value={edit.body}
            maxLength={10000}
            onChange={(e) => setEdit({ ...edit, body: e.target.value })}
            disabled={!editable}
          />
        </div>
        <div>
          <p className="text-xs font-medium text-muted-foreground">{t("attachments")}</p>
          <ul className="mt-1 space-y-0.5 text-sm">
            {data.email.attachments.map((a) => (
              <li key={a.document_id} className="flex items-center gap-1.5">
                <FileText aria-hidden className="size-3.5" />
                {a.name}
              </li>
            ))}
          </ul>
        </div>
        {editable ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant="outline" disabled={!dirty || save.isPending}>
              {save.isPending ? tc("saving") : tc("save")}
            </Button>
            <Button type="button" onClick={() => setConfirmOpen(true)} disabled={!canApprove}>
              {sending ? <Loader2 aria-hidden className="animate-spin" /> : <Send aria-hidden />}
              {t("approve")}
            </Button>
            {dirty ? <p className="text-xs text-muted-foreground">{t("saveFirst")}</p> : null}
          </div>
        ) : null}
      </form>

      <ApproveDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        data={data}
        duplicate={duplicate}
        flagged={flagged}
        onStarted={() => setSending(true)}
        onFinished={() => {
          setSending(false);
          void refresh();
        }}
      />
    </section>
  );
}

function ApproveDialog({
  open,
  onOpenChange,
  data,
  duplicate,
  flagged,
  onStarted,
  onFinished,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: Review;
  duplicate: boolean;
  flagged: boolean;
  onStarted: () => void;
  onFinished: () => void;
}) {
  const t = useTranslations("review");
  const [overrideDuplicate, setOverrideDuplicate] = useState(false);
  const [confirmFlagged, setConfirmFlagged] = useState(false);
  const ready = (!duplicate || overrideDuplicate) && (!flagged || confirmFlagged);

  const approve = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/outgoing/{email_id}/approve", {
          params: { path: { email_id: data.email.id } },
          body: { override_duplicate: overrideDuplicate, confirm_flagged: confirmFlagged },
        }),
      ),
    onSuccess: (r) => {
      onOpenChange(false);
      onStarted();
      followTask(
        r.task_id,
        (event) => {
          if (event.status === "succeeded") {
            toast.success(t("sentToast", { to: data.email.to_address ?? "" }));
            onFinished();
          } else if (event.status === "failed") {
            const code = sendError(event.error ?? "");
            toast.error(code ? t(`sendErrors.${code}`) : t("sendErrors.send_failed"));
            onFinished();
          }
        },
        onFinished,
      );
    },
    onError: () => {
      onOpenChange(false);
      toast.error(t("approveError"));
      onFinished();
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("confirm.title")}</DialogTitle>
          <DialogDescription>{t("confirm.body")}</DialogDescription>
        </DialogHeader>
        <dl className="space-y-1 text-sm">
          <div className="flex gap-2">
            <dt className="text-muted-foreground">{t("to")}</dt>
            <dd className="font-medium break-all">{data.email.to_address}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-muted-foreground">{t("subject")}</dt>
            <dd className="break-words">{data.email.subject}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-muted-foreground">{t("attachments")}</dt>
            <dd>{data.email.attachments.map((a) => a.name).join(", ")}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-muted-foreground">{t("from")}</dt>
            <dd className="break-all">{data.send_account}</dd>
          </div>
        </dl>
        {duplicate ? (
          <label className="flex items-start gap-2 text-sm">
            <Checkbox
              checked={overrideDuplicate}
              onCheckedChange={(v) => setOverrideDuplicate(v === true)}
              className="mt-0.5"
            />
            {t("confirm.duplicate")}
          </label>
        ) : null}
        {flagged ? (
          <label className="flex items-start gap-2 text-sm">
            <Checkbox
              checked={confirmFlagged}
              onCheckedChange={(v) => setConfirmFlagged(v === true)}
              className="mt-0.5"
            />
            {t("confirm.flagged")}
          </label>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("confirm.cancel")}
          </Button>
          <Button onClick={() => approve.mutate()} disabled={!ready || approve.isPending}>
            <Send aria-hidden />
            {t("confirm.send")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Changes({ data }: { data: Review }) {
  const t = useTranslations("review");
  if (!data.changes.length) return null;
  return (
    <section aria-labelledby="changes-title" className="space-y-3">
      <h3 id="changes-title" className="text-sm font-semibold">
        {t("changes.title")}
      </h3>
      <p className="text-xs text-muted-foreground">{t("changes.hint")}</p>
      <ul className="space-y-3">
        {data.changes.map((c) => (
          <li key={c.where} className="grid gap-2 rounded-lg border p-3 text-sm md:grid-cols-2">
            <div className="min-w-0">
              <p className="text-xs font-medium text-muted-foreground">
                {c.where === "headline" || c.where === "summary"
                  ? t(`changes.${c.where}`)
                  : t("changes.master")}
              </p>
              <p className="mt-0.5 break-words">{c.master || "—"}</p>
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-muted-foreground">{t("changes.tailored")}</p>
              <p className="mt-0.5 break-words">{c.tailored.replace(/\*\*/g, "")}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
