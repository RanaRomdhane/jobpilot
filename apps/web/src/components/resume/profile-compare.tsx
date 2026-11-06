"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, unwrap } from "@/lib/api/client";
import {
  isListSection,
  mergeProfiles,
  newItems,
  sameSection,
  SECTIONS,
  sectionSize,
  type Choice,
  type Profile,
  type SectionKey,
} from "@/lib/resume";
import { cn } from "@/lib/utils";

function summary(section: SectionKey, p: Profile): string[] {
  const v = p[section];
  if (!v) return [];
  if (typeof v === "string") return [v];
  if (section === "contact") return Object.values(v).filter(Boolean) as string[];
  return (v as Record<string, unknown>[]).map((item) =>
    [item.title, item.company, item.institution, item.degree, item.name, item.url]
      .filter((x) => typeof x === "string" && x)
      .slice(0, 2)
      .join(" · "),
  );
}

export function ProfileCompare({
  master,
  incoming,
  resumeId,
  alreadyMerged,
  onApplied,
}: {
  master: Profile;
  incoming: Profile;
  resumeId: number | null;
  alreadyMerged: boolean;
  onApplied: () => void;
}) {
  const t = useTranslations("resume.compare");
  const ts = useTranslations("resume.profile.sections");
  const tf = useTranslations("resume.profile.fields");
  const queryClient = useQueryClient();
  const [choices, setChoices] = useState<Partial<Record<SectionKey, Choice>>>({});
  const changed = Object.values(choices).some((c) => c && c !== "keep");
  const merged = useMemo(
    () => mergeProfiles(master, incoming, choices),
    [master, incoming, choices],
  );

  const apply = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/resume/master", { body: { profile: merged, source_resume_id: resumeId } }),
      ),
    onSuccess: async () => {
      toast.success(t("applied"));
      setChoices({});
      await queryClient.invalidateQueries({ queryKey: ["resume"] });
      onApplied();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="space-y-6">
      <p className="max-w-[70ch] text-sm text-muted-foreground">
        {alreadyMerged ? t("alreadyMerged") : t("intro")}
      </p>
      <ul className="divide-y border-y">
        {SECTIONS.map((section) => {
          const same = sameSection(section, master, incoming);
          const fresh = isListSection(section) ? newItems(section, master, incoming).length : 0;
          const options: Choice[] = isListSection(section)
            ? ["keep", "merge", "replace"]
            : ["keep", "replace"];
          const current = choices[section] ?? "keep";
          const label = section === "headline" || section === "summary" ? tf(section) : ts(section);
          return (
            <li
              key={section}
              className="grid gap-3 py-4 lg:grid-cols-[10rem_minmax(0,1fr)_minmax(0,1fr)_auto]"
            >
              <p className="font-medium">{label}</p>
              {(["master", "incoming"] as const).map((side) => {
                const p = side === "master" ? master : incoming;
                const lines = summary(section, p);
                return (
                  <div key={side} className="min-w-0 text-sm">
                    <p className="text-xs text-muted-foreground">
                      {t(side)} · {t("items", { count: sectionSize(section, p) })}
                      {side === "incoming" && isListSection(section) && !same
                        ? ` · ${t("newItems", { count: fresh })}`
                        : ""}
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {lines.slice(0, 4).map((l, i) => (
                        <li key={i} className="truncate">
                          {l}
                        </li>
                      ))}
                      {lines.length > 4 ? (
                        <li className="text-muted-foreground">+{lines.length - 4}</li>
                      ) : null}
                      {!lines.length ? (
                        <li className="text-muted-foreground">{t("empty")}</li>
                      ) : null}
                    </ul>
                  </div>
                );
              })}
              {same ? (
                <p className="self-start text-sm text-muted-foreground">{t("same")}</p>
              ) : (
                <div
                  role="radiogroup"
                  aria-label={t("choice", { section: label })}
                  className="flex flex-wrap gap-1 self-start"
                >
                  {options.map((o) => (
                    <button
                      key={o}
                      type="button"
                      role="radio"
                      aria-checked={current === o}
                      disabled={o === "merge" && fresh === 0}
                      onClick={() => setChoices((c) => ({ ...c, [section]: o }))}
                      className={cn(
                        "rounded-md border px-2.5 py-1 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-40",
                        current === o
                          ? "border-primary bg-primary text-primary-foreground"
                          : "hover:bg-muted",
                      )}
                    >
                      {t(o)}
                    </button>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <div className="flex justify-end">
        <Button onClick={() => apply.mutate()} disabled={!changed || apply.isPending}>
          {t("apply")}
        </Button>
      </div>
    </div>
  );
}
