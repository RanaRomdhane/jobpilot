"use client";

import { useMutation } from "@tanstack/react-query";
import { ChevronDown, CircleCheck, Quote, ShieldAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Section } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, unwrap } from "@/lib/api/client";
import { dateTime } from "@/lib/format";
import {
  CATEGORY_ORDER,
  ruleValues,
  scoreLevel,
  type AtsReport,
  type Breakdown,
  type Issue,
  type Severity,
} from "@/lib/resume";
import { cn } from "@/lib/utils";

const LEVEL_TONE = { good: "text-success", fair: "text-signal", poor: "text-destructive" };
const BAR_TONE = { good: "bg-success", fair: "bg-signal", poor: "bg-destructive" };
const SEVERITY_TONE: Record<Severity, string> = {
  critical: "border-destructive/50 text-destructive",
  high: "border-signal/60 text-signal",
  medium: "border-border text-foreground",
  low: "border-border text-muted-foreground",
};

export function ScoreBlock({ report }: { report: AtsReport }) {
  const t = useTranslations("resume.report");
  const b = report.breakdown as unknown as Breakdown;
  const level = scoreLevel(report.score);
  const categories = [...CATEGORY_ORDER, ...(b.keywords ? (["keywords"] as const) : [])];
  const value = (c: string) =>
    c === "keywords" ? Math.round((b.keywords?.coverage ?? 0) * 100) : (b.categories[c] ?? 0);

  return (
    <div className="grid gap-6 sm:grid-cols-[12rem_minmax(0,1fr)] sm:items-center">
      <div>
        <p className="text-sm text-muted-foreground">{t("score")}</p>
        <p className="mt-1 flex items-baseline gap-1">
          <span className={cn("text-5xl font-semibold tabular-nums", LEVEL_TONE[level])}>
            {report.score}
          </span>
          <span className="text-muted-foreground">/100</span>
        </p>
        <p className={cn("mt-1 text-sm font-medium", LEVEL_TONE[level])}>{t(`levels.${level}`)}</p>
      </div>
      <dl className="grid gap-2.5">
        {categories.map((c) => {
          const v = value(c);
          const tone = scoreLevel(v);
          return (
            <div
              key={c}
              className="grid grid-cols-[7.5rem_minmax(0,1fr)_2.5rem] items-center gap-3"
            >
              <dt className="truncate text-sm text-muted-foreground">{t(`categories.${c}`)}</dt>
              <dd className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                <div
                  className={cn("h-full rounded-full", BAR_TONE[tone])}
                  style={{ width: `${v}%` }}
                />
              </dd>
              <dd className="text-right text-sm tabular-nums">{v}</dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

function IssueItem({ issue }: { issue: Issue }) {
  const t = useTranslations("resume.report");
  const tr = useTranslations("resume.rules");
  const values = ruleValues(issue.params ?? {});
  const known = issue.source === "rule" && tr.has(`${issue.code}.title`);
  const title = known ? tr(`${issue.code}.title`, values) : (issue.message ?? issue.code);
  const fix = known ? tr(`${issue.code}.fix`, values) : issue.fix;
  return (
    <li className="grid gap-1.5 py-3.5 sm:grid-cols-[6.5rem_minmax(0,1fr)] sm:gap-4">
      <div className="flex items-start gap-2 sm:flex-col sm:gap-1">
        <span
          className={cn(
            "inline-flex rounded-md border px-1.5 py-0.5 text-xs font-medium",
            SEVERITY_TONE[issue.severity],
          )}
        >
          {t(`severity.${issue.severity}`)}
        </span>
        <span className="text-xs text-muted-foreground">{t(`source.${issue.source}`)}</span>
      </div>
      <div className="min-w-0 space-y-1">
        <p className="font-medium">{title}</p>
        {fix ? <p className="max-w-[70ch] text-sm text-muted-foreground">{fix}</p> : null}
        {issue.evidence ? (
          <p className="flex max-w-[70ch] gap-1.5 text-xs text-muted-foreground">
            <Quote aria-hidden className="mt-0.5 size-3 shrink-0" />
            <span className="sr-only">{t("evidence")} :</span>
            <span className="break-words italic">{issue.evidence}</span>
          </p>
        ) : null}
      </div>
    </li>
  );
}

function Keywords({ b }: { b: Breakdown }) {
  const t = useTranslations("resume.report.keywords");
  if (!b.keywords) return null;
  const pct = Math.round((b.keywords.coverage ?? 0) * 100);
  const chip = (k: { term: string; importance: string }, present: boolean) => (
    <li
      key={k.term}
      className={cn(
        "rounded-md border px-2 py-0.5 text-sm",
        present ? "border-success/40 text-foreground" : "border-signal/50 text-foreground",
      )}
    >
      {k.term}
      {k.importance === "required" ? (
        <span className="ml-1 text-xs text-muted-foreground">· {t("required")}</span>
      ) : null}
    </li>
  );
  return (
    <Section title={t("title")} aside={<span className="text-sm tabular-nums">{pct} %</span>}>
      <p className="mb-3 text-sm text-muted-foreground">
        {t("coverage", { percent: pct })}
        {b.target?.title ? ` · ${b.target.title}` : ""}
      </p>
      {b.target?.injection_flag ? (
        <p className="mb-3 flex items-center gap-2 text-sm text-signal">
          <ShieldAlert aria-hidden className="size-4" />
          {t("injection")}
        </p>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <p className="mb-2 text-sm font-medium">{t("missing")}</p>
          <ul className="flex flex-wrap gap-1.5">
            {b.keywords.missing.map((k) => chip(k, false))}
          </ul>
        </div>
        <div>
          <p className="mb-2 text-sm font-medium">{t("matched")}</p>
          <ul className="flex flex-wrap gap-1.5">{b.keywords.matched.map((k) => chip(k, true))}</ul>
        </div>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">{t("honest")}</p>
    </Section>
  );
}

function Review({ b }: { b: Breakdown }) {
  const t = useTranslations("resume.report.review");
  if (!b.review) {
    return b.review_error ? <p className="text-sm text-muted-foreground">{t("failed")}</p> : null;
  }
  const r = b.review;
  return (
    <Section title={t("title")}>
      {r.summary ? <p className="max-w-[70ch] text-sm">{r.summary}</p> : null}
      {r.strengths.length ? (
        <div className="mt-4">
          <p className="mb-1.5 text-sm font-medium">{t("strengths")}</p>
          <ul className="space-y-1 text-sm">
            {r.strengths.map((s) => (
              <li key={s} className="flex gap-2">
                <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-success" />
                {s}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {r.bullet_rewrites.length ? (
        <div className="mt-5">
          <p className="mb-1 text-sm font-medium">{t("rewrites")}</p>
          <p className="mb-3 text-xs text-muted-foreground">{t("placeholderHint")}</p>
          <ul className="divide-y rounded-lg border">
            {r.bullet_rewrites.map((rw) => (
              <li key={rw.original} className="grid gap-2 p-3 text-sm md:grid-cols-2 md:gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">{t("original")}</p>
                  <p className="mt-0.5">{rw.original}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{t("suggestion")}</p>
                  <p className="mt-0.5">
                    {rw.suggestion.split(/(\[[^\]]+\])/).map((part, i) =>
                      /^\[[^\]]+\]$/.test(part) ? (
                        <mark key={i} className="rounded bg-signal/15 px-0.5 text-foreground">
                          {part}
                        </mark>
                      ) : (
                        part
                      ),
                    )}
                  </p>
                  {rw.why ? <p className="mt-1 text-xs text-muted-foreground">{rw.why}</p> : null}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Section>
  );
}

export function JobAdForm({
  resumeId,
  onStarted,
}: {
  resumeId: number;
  onStarted: (taskId: string) => void;
}) {
  const t = useTranslations("resume.report.job");
  const locale = useLocale() as "fr" | "en";
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const run = useMutation({
    mutationFn: (body: { job_text: string | null; job_title: string | null }) =>
      unwrap(api.POST("/api/v1/ats/reports", { body: { resume_id: resumeId, locale, ...body } })),
    onSuccess: (r) => {
      toast.success(t("started"));
      onStarted(r.task_id);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (text.trim().length < 80) {
      setError(t("tooShort"));
      return;
    }
    run.mutate({ job_text: text.trim(), job_title: title.trim() || null });
  };

  return (
    <div className="rounded-lg border">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-3 rounded-lg p-4 text-left hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <span>
          <span className="block font-medium">{t("title")}</span>
          <span className="block text-sm text-muted-foreground">{t("description")}</span>
        </span>
        <ChevronDown
          aria-hidden
          className={cn("size-4 shrink-0 transition-transform", open && "rotate-180")}
        />
      </button>
      {open ? (
        <form onSubmit={submit} className="space-y-4 border-t p-4" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor="job-title">
              {t("jobTitle")} <span className="text-muted-foreground">· {t("jobTitleHint")}</span>
            </Label>
            <Input
              id="job-title"
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="job-text">{t("jobText")}</Label>
            <Textarea
              id="job-text"
              rows={8}
              value={text}
              maxLength={20000}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "job-text-error" : undefined}
              onChange={(e) => {
                setText(e.target.value);
                if (error) setError(null);
              }}
            />
            {error ? (
              <p id="job-text-error" className="text-xs text-destructive">
                {error}
              </p>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={run.isPending}>
              {t("analyze")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={run.isPending}
              onClick={() => run.mutate({ job_text: null, job_title: null })}
            >
              {t("rerun")}
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

export function AtsReportView({ report }: { report: AtsReport }) {
  const t = useTranslations("resume.report");
  const tf = useTranslations("resume.file");
  const locale = useLocale();
  const b = report.breakdown as unknown as Breakdown;
  const issues = report.issues as unknown as Issue[];
  return (
    <div className="space-y-10">
      <div className="space-y-2">
        <ScoreBlock report={report} />
        <p className="text-xs text-muted-foreground">
          {dateTime(report.generated_at, locale)} ·{" "}
          {t("stats", {
            words: b.stats.words ?? 0,
            pages: tf("pages", { count: b.stats.pages ?? 1 }),
          })}
        </p>
      </div>

      <Section
        title={t("fixes")}
        aside={<span className="text-sm tabular-nums">{issues.length}</span>}
      >
        {issues.length ? (
          <ol className="divide-y border-y">
            {issues.map((issue, i) => (
              <IssueItem key={`${issue.code}-${i}`} issue={issue} />
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted-foreground">{t("noFixes")}</p>
        )}
        {b.passed.length ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <CircleCheck aria-hidden className="size-4 text-success" />
            {t("passed", { count: b.passed.length })}
          </p>
        ) : null}
      </Section>

      <Keywords b={b} />
      <Review b={b} />
    </div>
  );
}
