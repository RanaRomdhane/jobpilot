"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { CategoryBadge, StatusLabel } from "@/components/badges";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api, unwrap, type ApplicationDetail } from "@/lib/api/client";
import { dateTime, relativeTime, shortDate } from "@/lib/format";

type Event = ApplicationDetail["events"][number];

function EventText({ event }: { event: Event }) {
  const t = useTranslations("overview.events");
  const ts = useTranslations("statuses");
  const tc = useTranslations("categories");
  const p = event.payload as Record<string, string | undefined>;
  switch (event.type) {
    case "status_changed":
      return (
        <>
          {t("status_changed", {
            from: ts((p.from ?? "sent") as never),
            to: ts((p.to ?? "sent") as never),
          })}
        </>
      );
    case "email_received":
      return <>{t("email_received", { category: tc((p.category ?? "Autre") as never) })}</>;
    case "created":
    case "email_linked":
    case "edited":
      return <>{t(event.type)}</>;
    default:
      return <>{event.type}</>;
  }
}

export function ApplicationDrawer({
  appId,
  onClose,
}: {
  appId: number | null;
  onClose: () => void;
}) {
  const t = useTranslations("applications.detail");
  const tc = useTranslations("common");
  const locale = useLocale();
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const app = useQuery({
    queryKey: ["application", appId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/applications/{app_id}", { params: { path: { app_id: appId! } } })),
    enabled: appId !== null,
  });
  useEffect(() => setNotes(app.data?.notes ?? ""), [app.data?.notes]);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["applications"] });
    void queryClient.invalidateQueries({ queryKey: ["application", appId] });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
  };
  const saveNotes = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/applications/{app_id}", {
          params: { path: { app_id: appId! } },
          body: { notes },
        }),
      ),
    onSuccess: () => {
      toast.success(t("notesSaved"));
      invalidate();
    },
    onError: (e) => toast.error(String(e)),
  });
  const remove = useMutation({
    mutationFn: () =>
      api.DELETE("/api/v1/applications/{app_id}", { params: { path: { app_id: appId! } } }),
    onSuccess: () => {
      toast.success(t("deleted"));
      setConfirmDelete(false);
      invalidate();
      onClose();
    },
  });

  const a = app.data;
  return (
    <>
      <Sheet open={appId !== null} onOpenChange={(open) => !open && onClose()}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle className="pr-6 text-left text-lg leading-snug">
              {a?.role_title ?? "…"}
            </SheetTitle>
            <SheetDescription className="text-left">
              {a
                ? `${a.company?.name ?? "—"}${a.company?.domain ? ` · ${a.company.domain}` : ""}`
                : ""}
            </SheetDescription>
          </SheetHeader>
          {!a ? (
            <div className="space-y-3 px-4" aria-busy="true">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : (
            <div className="space-y-7 px-4 pb-6">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <StatusLabel status={a.status} />
                {a.applied_at ? (
                  <span className="text-muted-foreground">
                    {t("applied", { date: shortDate(a.applied_at, locale) })}
                  </span>
                ) : null}
                {a.interview_at ? (
                  <span className="font-medium text-cat-entretien">
                    {t("interview", { date: dateTime(a.interview_at, locale) })}
                  </span>
                ) : null}
                {a.apply_url ? (
                  <a
                    href={a.apply_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-primary hover:underline"
                  >
                    <ExternalLink aria-hidden className="size-3.5" />
                    {a.source ?? "lien"}
                  </a>
                ) : null}
              </div>

              <section aria-labelledby="timeline" className="space-y-3">
                <h3 id="timeline" className="text-sm font-semibold">
                  {t("timeline")}
                </h3>
                <ol className="relative space-y-4 border-l pl-4">
                  {a.events.map((e) => (
                    <li key={e.id} className="relative text-sm">
                      <span
                        aria-hidden
                        className="absolute top-1.5 -left-[1.3rem] size-2 rounded-full bg-primary"
                      />
                      <span className="font-medium">
                        <EventText event={e} />
                      </span>
                      {(e.payload as { by?: string }).by === "auto" ? (
                        <span className="ml-1 text-xs text-muted-foreground">(auto)</span>
                      ) : null}
                      <time
                        dateTime={e.occurred_at}
                        className="block text-xs text-muted-foreground"
                      >
                        {relativeTime(e.occurred_at, locale)}
                      </time>
                    </li>
                  ))}
                </ol>
              </section>

              <section aria-labelledby="emails" className="space-y-2">
                <h3 id="emails" className="text-sm font-semibold">
                  {t("emails")}
                </h3>
                {a.emails.length ? (
                  <ul className="divide-y">
                    {a.emails.map((m) => (
                      <li
                        key={m.id}
                        className="flex items-start justify-between gap-3 py-2 text-sm"
                      >
                        <a href={`/inbox?open=${m.id}`} className="min-w-0 hover:underline">
                          <span className="block truncate font-medium">{m.subject}</span>
                          <span className="block text-xs text-muted-foreground">
                            {relativeTime(m.received_at, locale)}
                          </span>
                        </a>
                        <CategoryBadge category={m.category} />
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">{t("noEmails")}</p>
                )}
              </section>

              <section className="space-y-2">
                <Label htmlFor="app-notes" className="text-sm font-semibold">
                  {t("notes")}
                </Label>
                <Textarea
                  id="app-notes"
                  rows={4}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => saveNotes.mutate()}
                  disabled={saveNotes.isPending || notes === (a.notes ?? "")}
                >
                  {saveNotes.isPending ? tc("saving") : t("saveNotes")}
                </Button>
              </section>

              <div className="border-t pt-4">
                <Button
                  variant="ghost"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 aria-hidden className="size-4" />
                  {t("delete")}
                </Button>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("delete")}</DialogTitle>
            <DialogDescription>
              {t("deleteConfirm", { role: a?.role_title ?? "", company: a?.company?.name ?? "—" })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
            >
              {t("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
