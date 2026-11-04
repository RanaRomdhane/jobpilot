"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  CONTRACTS,
  DOMAINS,
  MIN_SCORES,
  POSTED_WITHIN,
  REGIONS,
  SENIORITY,
  WORK_MODES,
  splitWords,
  toggle,
  type Filters,
} from "@/lib/jobs";
import { cn } from "@/lib/utils";

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-2">
      <legend className="mb-2 text-sm font-medium">{title}</legend>
      {children}
    </fieldset>
  );
}

/** A multi-select as a row of toggle chips (aria-pressed), keyboard reachable. */
function Chips<T extends string>({
  values,
  selected,
  label,
  onToggle,
}: {
  values: readonly T[];
  selected: readonly T[] | undefined;
  label: (v: T) => string;
  onToggle: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((v) => {
        const on = selected?.includes(v) ?? false;
        return (
          <button
            key={v}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(v)}
            className={cn(
              "rounded-md border px-2.5 py-1 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              on ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
            )}
          >
            {label(v)}
          </button>
        );
      })}
    </div>
  );
}

function SelectRow({
  id,
  label,
  value,
  onChange,
  options,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none dark:bg-input/30"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/** Words typed with commas: committed on blur / Enter so the list does not refetch per key. */
function WordsField({
  id,
  label,
  hint,
  value,
  onCommit,
}: {
  id: string;
  label: string;
  hint: string;
  value: string[] | undefined;
  onCommit: (words: string[]) => void;
}) {
  const [draft, setDraft] = useState((value ?? []).join(", "));
  useEffect(() => setDraft((value ?? []).join(", ")), [value]);
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={draft}
        aria-describedby={`${id}-hint`}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => onCommit(splitWords(draft))}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onCommit(splitWords(draft));
          }
        }}
      />
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {hint}
      </p>
    </div>
  );
}

export function FilterPanel({
  filters,
  onChange,
  onReset,
  onClear,
  onSavePreferences,
  savingPreferences,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
  onReset: () => void;
  onClear: () => void;
  onSavePreferences: () => void;
  savingPreferences: boolean;
}) {
  const t = useTranslations("jobs");
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  return (
    <div className="space-y-6">
      <Group title={t("filters.region")}>
        <Chips
          values={REGIONS}
          selected={filters.regions}
          label={(v) => t(`regions.${v}`)}
          onToggle={(v) => set({ regions: toggle(filters.regions, v) })}
        />
      </Group>
      <Group title={t("filters.domain")}>
        <Chips
          values={DOMAINS}
          selected={filters.domains}
          label={(v) => t(`domains.${v}`)}
          onToggle={(v) => set({ domains: toggle(filters.domains, v) })}
        />
      </Group>
      <Group title={t("filters.workMode")}>
        <Chips
          values={WORK_MODES}
          selected={filters.work_modes}
          label={(v) => t(`workModes.${v}`)}
          onToggle={(v) => set({ work_modes: toggle(filters.work_modes, v) })}
        />
      </Group>
      <Group title={t("filters.seniority")}>
        <Chips
          values={SENIORITY}
          selected={filters.seniority}
          label={(v) => t(`seniority.${v}`)}
          onToggle={(v) => set({ seniority: toggle(filters.seniority, v) })}
        />
      </Group>
      <Group title={t("filters.contract")}>
        <Chips
          values={CONTRACTS}
          selected={filters.contracts}
          label={(v) => t(`contracts.${v}`)}
          onToggle={(v) => set({ contracts: toggle(filters.contracts, v) })}
        />
      </Group>
      <Group title={t("filters.language")}>
        <Chips
          values={["fr", "en", "ar"] as const}
          selected={filters.languages as ("fr" | "en" | "ar")[] | undefined}
          label={(v) => t(`languages.${v}`)}
          onToggle={(v) => set({ languages: toggle(filters.languages, v) })}
        />
      </Group>
      <div className="grid gap-4">
        <SelectRow
          id="f-posted"
          label={t("filters.posted")}
          value={String(filters.posted_within_days ?? "")}
          onChange={(v) => set({ posted_within_days: v ? Number(v) : null })}
          options={[
            { value: "", label: t("filters.postedAny") },
            ...POSTED_WITHIN.map((d) => ({
              value: String(d),
              label: t("filters.postedDays", { count: d }),
            })),
          ]}
        />
        <SelectRow
          id="f-score"
          label={t("filters.minScore")}
          value={String(filters.min_score ?? "")}
          onChange={(v) => set({ min_score: v ? Number(v) : null })}
          options={[
            { value: "", label: t("filters.minScoreAny") },
            ...MIN_SCORES.map((s) => ({ value: String(s), label: `≥ ${s}` })),
          ]}
        />
        <WordsField
          id="f-include"
          label={t("filters.include")}
          hint={t("filters.wordsHint")}
          value={filters.include}
          onCommit={(include) => set({ include })}
        />
        <WordsField
          id="f-exclude"
          label={t("filters.exclude")}
          hint={t("filters.wordsHint")}
          value={filters.exclude}
          onCommit={(exclude) => set({ exclude })}
        />
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={filters.include_unknown}
            onChange={(e) => set({ include_unknown: e.target.checked })}
          />
          <span>
            {t("filters.includeUnknown")}
            <span className="block text-xs text-muted-foreground">
              {t("filters.includeUnknownHint")}
            </span>
          </span>
        </label>
      </div>
      <div className="flex flex-wrap gap-2 border-t pt-4">
        <Button type="button" variant="outline" size="sm" onClick={onReset}>
          {t("filters.reset")}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onClear}>
          {t("filters.clear")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={savingPreferences}
          onClick={onSavePreferences}
        >
          {t("filters.savePrefs")}
        </Button>
      </div>
    </div>
  );
}
