"use client";

import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, MoreHorizontal, Plus, Sparkles } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { ApplicationDrawer } from "@/components/application-drawer";
import { NewApplicationDialog } from "@/components/application-form";
import { StatusLabel, STATUS_TONE } from "@/components/badges";
import { ErrorState, PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { STATUSES, api, unwrap, type Application, type ApplicationStatus } from "@/lib/api/client";
import { dateTime, relativeTime, shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Columns of the board; draft/review appear once there is something in them (phase 6–7). */
const BOARD: ApplicationStatus[] = [
  "sent",
  "recruiter_contact",
  "interview",
  "offer",
  "rejected",
  "ghosted",
  "withdrawn",
];

function useMoveApplication() {
  const t = useTranslations("applications");
  const ts = useTranslations("statuses");
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: number; status: ApplicationStatus }) =>
      unwrap(
        api.PATCH("/api/v1/applications/{app_id}/status", {
          params: { path: { app_id: id } },
          body: { status },
        }),
      ),
    onMutate: async ({ id, status }) => {
      await queryClient.cancelQueries({ queryKey: ["applications"] });
      const previous = queryClient.getQueryData<Application[]>(["applications"]);
      queryClient.setQueryData<Application[]>(["applications"], (old) =>
        old?.map((a) => (a.id === id ? { ...a, status } : a)),
      );
      return { previous };
    },
    onError: (e, _, ctx) => {
      queryClient.setQueryData(["applications"], ctx?.previous);
      toast.error(String(e));
    },
    onSuccess: (a) => toast.success(t("moved", { status: ts(a.status) })),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
  });
}

function MoveMenu({ app }: { app: Application }) {
  const t = useTranslations("applications");
  const ts = useTranslations("statuses");
  const move = useMoveApplication();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          aria-label={`${t("moveTo")} ${app.role_title}`}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <MoreHorizontal aria-hidden className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>{t("moveTo")}</DropdownMenuLabel>
        {STATUSES.filter((s) => s !== app.status).map((s) => (
          <DropdownMenuItem key={s} onSelect={() => move.mutate({ id: app.id, status: s })}>
            {ts(s)}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Card({ app, onOpen }: { app: Application; onOpen: () => void }) {
  const locale = useLocale();
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: app.id,
    data: { status: app.status },
  });
  return (
    <li
      ref={setNodeRef}
      style={
        transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined
      }
      className={cn(
        "group rounded-md border bg-card p-3 shadow-[0_1px_0_var(--border)]",
        isDragging && "relative z-20 rotate-1 shadow-lg ring-2 ring-primary",
      )}
    >
      <div className="flex items-start gap-2">
        <button
          type="button"
          {...listeners}
          {...attributes}
          onClick={onOpen}
          className="min-w-0 flex-1 cursor-grab text-left active:cursor-grabbing"
        >
          <span className="block truncate text-sm font-medium">{app.role_title}</span>
          <span className="block truncate text-sm text-muted-foreground">
            {app.company?.name ?? "—"}
          </span>
        </button>
        <MoveMenu app={app} />
      </div>
      <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
        {app.interview_at ? (
          <span className="inline-flex items-center gap-1 font-medium text-cat-entretien">
            <CalendarClock aria-hidden className="size-3.5" />
            {dateTime(app.interview_at, locale)}
          </span>
        ) : (
          <span>{relativeTime(app.updated_at, locale)}</span>
        )}
        {app.pending_suggestions ? (
          <span className="ml-auto inline-flex items-center gap-1 font-medium text-signal">
            <Sparkles aria-hidden className="size-3.5" />
            {app.pending_suggestions}
          </span>
        ) : null}
      </div>
    </li>
  );
}

function Column({
  status,
  apps,
  onOpen,
}: {
  status: ApplicationStatus;
  apps: Application[];
  onOpen: (id: number) => void;
}) {
  const t = useTranslations("applications");
  const ts = useTranslations("statuses");
  const { setNodeRef, isOver } = useDroppable({ id: status });
  return (
    <section
      ref={setNodeRef}
      aria-label={ts(status)}
      className={cn(
        "flex w-[17rem] shrink-0 snap-start flex-col rounded-lg bg-muted/50 p-2 transition-colors",
        isOver && "bg-accent ring-2 ring-primary/40",
      )}
    >
      <h2 className="flex items-baseline justify-between px-1.5 pb-2 text-sm">
        <span className={cn("font-semibold", STATUS_TONE[status])}>{ts(status)}</span>
        <span className="text-muted-foreground tabular-nums">{apps.length}</span>
      </h2>
      <ul className="flex min-h-16 flex-1 flex-col gap-2">
        {apps.length ? (
          apps.map((a) => <Card key={a.id} app={a} onOpen={() => onOpen(a.id)} />)
        ) : (
          <li className="rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">
            {t("emptyColumn")}
          </li>
        )}
      </ul>
    </section>
  );
}

function Board({ apps, onOpen }: { apps: Application[]; onOpen: (id: number) => void }) {
  const move = useMoveApplication();
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor),
  );
  const columns = [
    ...(["draft", "ready_for_review"] as ApplicationStatus[]).filter((s) =>
      apps.some((a) => a.status === s),
    ),
    ...BOARD,
  ];
  function onDragEnd(e: DragEndEvent) {
    const to = e.over?.id as ApplicationStatus | undefined;
    const from = e.active.data.current?.status as ApplicationStatus | undefined;
    if (to && from && to !== from) move.mutate({ id: Number(e.active.id), status: to });
  }
  return (
    <DndContext sensors={sensors} onDragEnd={onDragEnd}>
      <div className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-4 sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0">
        {columns.map((s) => (
          <Column key={s} status={s} apps={apps.filter((a) => a.status === s)} onOpen={onOpen} />
        ))}
      </div>
    </DndContext>
  );
}

function TableView({ apps, onOpen }: { apps: Application[]; onOpen: (id: number) => void }) {
  const t = useTranslations("applications.columns");
  const locale = useLocale();
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th scope="col" className="pb-2 font-medium">
              {t("role")}
            </th>
            <th scope="col" className="pb-2 font-medium">
              {t("company")}
            </th>
            <th scope="col" className="pb-2 font-medium">
              {t("status")}
            </th>
            <th scope="col" className="pb-2 font-medium">
              {t("applied")}
            </th>
            <th scope="col" className="pb-2 text-right font-medium">
              {t("updated")}
            </th>
          </tr>
        </thead>
        <tbody>
          {apps.map((a) => (
            <tr
              key={a.id}
              className="cursor-pointer border-b hover:bg-muted/60"
              onClick={() => onOpen(a.id)}
            >
              <td className="py-2.5 pr-4">
                <button
                  type="button"
                  className="text-left font-medium"
                  onClick={() => onOpen(a.id)}
                >
                  {a.role_title}
                </button>
              </td>
              <td className="py-2.5 pr-4 text-muted-foreground">{a.company?.name ?? "—"}</td>
              <td className="py-2.5 pr-4">
                <StatusLabel status={a.status} />
              </td>
              <td className="py-2.5 pr-4 text-muted-foreground">
                {shortDate(a.applied_at, locale)}
              </td>
              <td className="py-2.5 text-right text-muted-foreground">
                {relativeTime(a.updated_at, locale)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SuggestionsBanner() {
  const t = useTranslations("applications");
  const ts = useTranslations("statuses");
  const queryClient = useQueryClient();
  const suggestions = useQuery({
    queryKey: ["suggestions"],
    queryFn: () => unwrap(api.GET("/api/v1/suggestions")),
  });
  const resolve = useMutation({
    mutationFn: ({ id, accept }: { id: number; accept: boolean }) =>
      unwrap(
        accept
          ? api.POST("/api/v1/suggestions/{suggestion_id}/accept", {
              params: { path: { suggestion_id: id } },
            })
          : api.POST("/api/v1/suggestions/{suggestion_id}/dismiss", {
              params: { path: { suggestion_id: id } },
            }),
      ),
    onSuccess: (_, v) => {
      toast.success(v.accept ? t("accepted") : t("dismissed"));
      void queryClient.invalidateQueries();
    },
    onError: (e) => toast.error(String(e)),
  });
  if (!suggestions.data?.length) return null;
  return (
    <section
      aria-labelledby="suggestions"
      className="mb-6 rounded-lg border border-signal/40 bg-signal/5 p-3"
    >
      <h2 id="suggestions" className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <Sparkles aria-hidden className="size-4 text-signal" />
        {t("suggestions")}
      </h2>
      <ul className="divide-y divide-signal/20">
        {suggestions.data.map((s) => (
          <li
            key={s.id}
            className="flex flex-col gap-2 py-2 sm:flex-row sm:items-center sm:justify-between"
          >
            <span className="text-sm">
              {t("suggestionLine", {
                company: s.company_name ?? "—",
                role: s.application_role ?? "—",
                from: ts(s.from_status),
                to: ts(s.to_status),
              })}
              {s.email_subject ? (
                <span className="block text-xs text-muted-foreground">« {s.email_subject} »</span>
              ) : null}
            </span>
            <span className="flex shrink-0 gap-2">
              <Button
                size="sm"
                onClick={() => resolve.mutate({ id: s.id, accept: true })}
                disabled={resolve.isPending}
              >
                {t("accept")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => resolve.mutate({ id: s.id, accept: false })}
                disabled={resolve.isPending}
              >
                {t("dismiss")}
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ApplicationsView() {
  const t = useTranslations("applications");
  const tc = useTranslations("common");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [creating, setCreating] = useState(false);
  const openId = params.get("open") ? Number(params.get("open")) : null;
  const setOpen = (id: number | null) => {
    const next = new URLSearchParams(params.toString());
    if (id === null) next.delete("open");
    else next.set("open", String(id));
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  const apps = useQuery({
    queryKey: ["applications"],
    queryFn: () => unwrap(api.GET("/api/v1/applications")),
  });

  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("subtitle")}
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus aria-hidden className="size-4" />
            {t("add")}
          </Button>
        }
      />
      <SuggestionsBanner />

      {apps.isError ? (
        <ErrorState
          message={tc("loadError")}
          onRetry={() => apps.refetch()}
          retryLabel={tc("retry")}
        />
      ) : !apps.data ? (
        <div className="flex gap-3 overflow-hidden" aria-busy="true">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-64 w-[17rem] shrink-0" />
          ))}
        </div>
      ) : apps.data.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center">
          <p className="mb-4 text-sm text-muted-foreground">{t("empty")}</p>
          <Button onClick={() => setCreating(true)}>
            <Plus aria-hidden className="size-4" />
            {t("add")}
          </Button>
        </div>
      ) : (
        <Tabs defaultValue="board">
          <TabsList className="mb-4">
            <TabsTrigger value="board">{t("board")}</TabsTrigger>
            <TabsTrigger value="table">{t("table")}</TabsTrigger>
          </TabsList>
          <TabsContent value="board">
            <Board apps={apps.data} onOpen={setOpen} />
          </TabsContent>
          <TabsContent value="table">
            <TableView apps={apps.data} onOpen={setOpen} />
          </TabsContent>
        </Tabs>
      )}

      <NewApplicationDialog open={creating} onOpenChange={setCreating} />
      <ApplicationDrawer appId={openId} onClose={() => setOpen(null)} />
    </>
  );
}

export default function ApplicationsPage() {
  return (
    <Suspense>
      <ApplicationsView />
    </Suspense>
  );
}
