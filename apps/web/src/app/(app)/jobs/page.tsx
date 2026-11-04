"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Radio, RefreshCw, SlidersHorizontal } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { FilterPanel } from "@/components/jobs/filter-panel";
import { JobCard } from "@/components/jobs/job-card";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { SavedSearches } from "@/components/jobs/saved-searches";
import { SourcesDialog } from "@/components/jobs/sources-dialog";
import { ErrorState, PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api, unwrap } from "@/lib/api/client";
import { followTask } from "@/lib/api/task-stream";
import {
  activeFilterCount,
  EMPTY_FILTERS,
  normalizeFilters,
  type Filters,
  type Job,
} from "@/lib/jobs";

const PAGE = 30;

export default function JobsPage() {
  const t = useTranslations("jobs");
  const tn = useTranslations("nav");
  const tc = useTranslations("common");
  const locale = useLocale() as "fr" | "en";
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<Filters | null>(null);
  const [sort, setSort] = useState<"match" | "date">("match");
  const [savedOnly, setSavedOnly] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [openJob, setOpenJob] = useState<number | null>(null);
  const [mobileFilters, setMobileFilters] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [running, setRunning] = useState<{ progress: number; message: string | null } | null>(null);

  const prefs = useQuery({
    queryKey: ["jobs", "preferences"],
    queryFn: () => unwrap(api.GET("/api/v1/jobs/preferences")),
  });
  useEffect(() => {
    if (filters === null && prefs.data) setFilters(normalizeFilters(prefs.data as Filters));
  }, [prefs.data, filters]);

  const active = filters ?? EMPTY_FILTERS;
  const search = useQuery({
    queryKey: ["jobs", "search", active, sort, savedOnly, limit],
    queryFn: () =>
      unwrap(
        api.POST("/api/v1/jobs/search", {
          body: {
            filters: active,
            sort,
            saved_only: savedOnly,
            include_hidden: false,
            limit,
            offset: 0,
          },
        }),
      ),
    enabled: filters !== null,
    placeholderData: keepPreviousData,
  });

  const changeFilters = (f: Filters) => {
    setFilters(f);
    setLimit(PAGE);
  };

  const follow = (taskId: string) => {
    setRunning({ progress: 0, message: null });
    followTask(
      taskId,
      (event) => {
        setRunning({ progress: event.progress ?? 0, message: event.message ?? null });
        if (["succeeded", "failed", "cancelled"].includes(event.status)) {
          setRunning(null);
          void queryClient.invalidateQueries({ queryKey: ["jobs"] });
        }
      },
      () => setRunning(null),
    );
  };

  const refresh = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/jobs/refresh", { body: { locale } })),
    onSuccess: (r) => {
      toast.success(t("refreshStarted"));
      follow(r.task_id);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const flag = useMutation({
    mutationFn: ({ id, ...body }: { id: number; saved?: boolean; hidden?: boolean }) =>
      unwrap(api.PATCH("/api/v1/jobs/{job_id}", { params: { path: { job_id: id } }, body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", "search"] }),
    onError: (e: Error) => toast.error(e.message),
  });
  const hide = (job: Job) => {
    flag.mutate(
      { id: job.id, hidden: true },
      {
        onSuccess: () =>
          toast(t("card.hidden"), {
            action: {
              label: t("card.undo"),
              onClick: () => flag.mutate({ id: job.id, hidden: false }),
            },
          }),
      },
    );
  };

  const savePrefs = useMutation({
    mutationFn: (f: Filters) => unwrap(api.PUT("/api/v1/jobs/preferences", { body: f })),
    onSuccess: async () => {
      toast.success(t("filters.prefsSaved"));
      await queryClient.invalidateQueries({ queryKey: ["jobs", "preferences"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const panel = (
    <FilterPanel
      filters={active}
      onChange={changeFilters}
      onReset={() => prefs.data && changeFilters(normalizeFilters(prefs.data as Filters))}
      onClear={() => changeFilters(EMPTY_FILTERS)}
      onSavePreferences={() => savePrefs.mutate(active)}
      savingPreferences={savePrefs.isPending}
    />
  );

  const actions = (
    <>
      <Button variant="outline" onClick={() => setSourcesOpen(true)}>
        <Radio aria-hidden />
        {t("sourcesButton")}
      </Button>
      <Button onClick={() => refresh.mutate()} disabled={refresh.isPending || running !== null}>
        <RefreshCw
          aria-hidden
          className={running ? "animate-spin motion-reduce:animate-none" : ""}
        />
        {running ? t("refreshing") : t("refresh")}
      </Button>
    </>
  );

  if (prefs.isError || search.isError) {
    return (
      <>
        <PageHeader title={tn("jobs")} description={t("description")} actions={actions} />
        <ErrorState
          message={tc("loadError")}
          retryLabel={tc("retry")}
          onRetry={() => {
            void prefs.refetch();
            void search.refetch();
          }}
        />
      </>
    );
  }

  const page = search.data;
  const count = activeFilterCount(active);

  return (
    <>
      <PageHeader title={tn("jobs")} description={t("description")} actions={actions} />

      {running ? (
        <div role="status" className="mb-6 rounded-lg border p-3">
          <div className="mb-2 flex items-baseline justify-between gap-3 text-sm">
            <span className="font-medium">{t("refreshing")}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-300"
              style={{ width: `${Math.max(4, Math.round(running.progress * 100))}%` }}
            />
          </div>
        </div>
      ) : null}

      {page && !page.profile_ready ? (
        <p className="mb-6 rounded-lg border border-signal/40 bg-signal/5 p-3 text-sm">
          {t("noProfile")}{" "}
          <Link href="/resume" className="font-medium text-primary hover:underline">
            {t("noProfileLink")}
          </Link>
        </p>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <aside className="hidden lg:block" aria-label={t("filters.title")}>
          <div className="sticky top-6 max-h-[calc(100vh-3rem)] overflow-y-auto pr-1">{panel}</div>
        </aside>

        <section aria-label={tn("jobs")} className="min-w-0 space-y-4">
          <SavedSearches filters={active} onApply={changeFilters} />

          <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
            <p className="text-sm font-medium" aria-live="polite">
              {page ? t("results", { count: page.total }) : " "}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="lg:hidden"
                onClick={() => setMobileFilters(true)}
              >
                <SlidersHorizontal aria-hidden />
                {t("filtersButton")}
                {count ? <span className="tabular-nums">({count})</span> : null}
              </Button>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={savedOnly}
                  onChange={(e) => setSavedOnly(e.target.checked)}
                />
                {t("savedOnly")}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">{t("sort.label")}</span>
                <select
                  value={sort}
                  onChange={(e) => setSort(e.target.value as "match" | "date")}
                  className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm dark:bg-input/30"
                >
                  <option value="match">{t("sort.match")}</option>
                  <option value="date">{t("sort.date")}</option>
                </select>
              </label>
            </div>
          </div>

          {!page ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }, (_, i) => (
                <Skeleton key={i} className="h-24 w-full" />
              ))}
            </div>
          ) : page.items.length === 0 ? (
            <div className="rounded-lg border border-dashed p-8 text-center">
              {page.total === 0 && count === 0 && !savedOnly ? (
                <>
                  <p className="font-medium">{t("empty.title")}</p>
                  <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                    {t("empty.body")}
                  </p>
                  <Button
                    className="mt-4"
                    onClick={() => refresh.mutate()}
                    disabled={refresh.isPending || running !== null}
                  >
                    <RefreshCw aria-hidden />
                    {t("refresh")}
                  </Button>
                </>
              ) : (
                <>
                  <p className="text-sm text-muted-foreground">{t("empty.filtered")}</p>
                  <Button
                    className="mt-3"
                    variant="outline"
                    onClick={() => changeFilters(EMPTY_FILTERS)}
                  >
                    {t("empty.clear")}
                  </Button>
                </>
              )}
            </div>
          ) : (
            <>
              <ul className="divide-y">
                {page.items.map((job) => (
                  <JobCard
                    key={job.id}
                    job={job}
                    onOpen={() => setOpenJob(job.id)}
                    onSave={() => flag.mutate({ id: job.id, saved: !job.saved })}
                    onHide={() => hide(job)}
                  />
                ))}
              </ul>
              {page.items.length < page.total ? (
                <div className="flex justify-center">
                  <Button variant="outline" onClick={() => setLimit((l) => l + PAGE)}>
                    {t("more")}
                  </Button>
                </div>
              ) : null}
            </>
          )}
        </section>
      </div>

      <Sheet open={mobileFilters} onOpenChange={setMobileFilters}>
        <SheetContent side="left" className="w-full overflow-y-auto sm:max-w-sm">
          <SheetHeader>
            <SheetTitle>{t("filters.title")}</SheetTitle>
          </SheetHeader>
          <div className="px-4 pb-6">
            {panel}
            <Button className="mt-4 w-full" onClick={() => setMobileFilters(false)}>
              {t("filters.apply")}
            </Button>
          </div>
        </SheetContent>
      </Sheet>

      <JobDrawer jobId={openJob} onClose={() => setOpenJob(null)} onTask={follow} />
      <SourcesDialog open={sourcesOpen} onOpenChange={setSourcesOpen} />
    </>
  );
}
