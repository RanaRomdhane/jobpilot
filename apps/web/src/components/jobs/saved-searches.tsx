"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellPlus, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, unwrap } from "@/lib/api/client";
import { normalizeFilters, type Filters, type SavedSearch } from "@/lib/jobs";
import { cn } from "@/lib/utils";

export function SavedSearches({
  filters,
  onApply,
}: {
  filters: Filters;
  onApply: (f: Filters) => void;
}) {
  const t = useTranslations("jobs.saved");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const list = useQuery({
    queryKey: ["jobs", "saved-searches"],
    queryFn: () => unwrap(api.GET("/api/v1/saved-searches")),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["jobs", "saved-searches"] });

  const create = useMutation({
    mutationFn: (n: string) =>
      unwrap(api.POST("/api/v1/saved-searches", { body: { name: n, filters, enabled: true } })),
    onSuccess: async () => {
      toast.success(t("created"));
      setNaming(false);
      setName("");
      await refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const seen = useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.POST("/api/v1/saved-searches/{search_id}/seen", {
          params: { path: { search_id: id } },
        }),
      ),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.DELETE("/api/v1/saved-searches/{search_id}", { params: { path: { search_id: id } } }),
      ),
    onSuccess: async () => {
      toast.success(t("deleted"));
      await refresh();
    },
  });

  const apply = (s: SavedSearch) => {
    onApply(normalizeFilters(s.filters as Filters));
    if (s.new_match_count) seen.mutate(s.id);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {list.data?.map((s) => (
        <span key={s.id} className="inline-flex items-center rounded-md border text-sm">
          <button
            type="button"
            onClick={() => apply(s)}
            aria-label={t("apply", { name: s.name })}
            className="rounded-l-md px-2.5 py-1 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {s.name}
            {s.new_match_count ? (
              <span className="ml-1.5 rounded bg-signal/15 px-1 text-xs font-medium text-signal">
                {t("newCount", { count: s.new_match_count })}
              </span>
            ) : null}
          </button>
          <button
            type="button"
            onClick={() => remove.mutate(s.id)}
            aria-label={t("delete", { name: s.name })}
            className={cn(
              "rounded-r-md border-l px-1.5 py-1 text-muted-foreground hover:bg-muted hover:text-foreground",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
            )}
          >
            <X aria-hidden className="size-3.5" />
          </button>
        </span>
      ))}
      <Button type="button" variant="ghost" size="sm" onClick={() => setNaming(true)}>
        <BellPlus aria-hidden />
        {t("save")}
      </Button>

      <Dialog open={naming} onOpenChange={setNaming}>
        <DialogContent>
          <form onSubmit={submit} className="space-y-4">
            <DialogHeader>
              <DialogTitle>{t("save")}</DialogTitle>
            </DialogHeader>
            <div className="space-y-1.5">
              <Label htmlFor="saved-name">{t("namePrompt")}</Label>
              <Input
                id="saved-name"
                value={name}
                maxLength={200}
                placeholder={t("namePlaceholder")}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setNaming(false)}>
                {tc("cancel")}
              </Button>
              <Button type="submit" disabled={!name.trim() || create.isPending}>
                {t("save")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
