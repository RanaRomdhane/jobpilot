"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";

import { ErrorState, PageHeader, Section } from "@/components/page-header";
import { SystemStatus } from "@/components/system-status";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { Textarea } from "@/components/ui/textarea";
import { setLocale } from "@/i18n/actions";
import { api, unwrap, type Preferences, type SettingsOut } from "@/lib/api/client";

type Purpose = "triage" | "guard" | "parse_resume" | "rerank" | "tailor" | "cover_letter";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 py-2.5 sm:grid-cols-[14rem_1fr] sm:gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-sm break-words">{children}</dd>
    </div>
  );
}

function Ok({ ok, children }: { ok: boolean; children: ReactNode }) {
  const Icon = ok ? CircleCheck : CircleAlert;
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon aria-hidden className={ok ? "size-4 text-success" : "size-4 text-signal"} />
      {children}
    </span>
  );
}

function PreferencesForm({ initial }: { initial: Preferences }) {
  const t = useTranslations("settings");
  const tc = useTranslations("common");
  const tl = useTranslations("locale");
  const router = useRouter();
  const queryClient = useQueryClient();
  const [prefs, setPrefs] = useState(initial);
  useEffect(() => setPrefs(initial), [initial]);

  const save = useMutation({
    mutationFn: (p: Preferences) => unwrap(api.PUT("/api/v1/settings/preferences", { body: p })),
    onSuccess: async (p) => {
      toast.success(t("saved"));
      await setLocale(p.locale ?? "fr");
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      router.refresh();
    },
    onError: (e) => toast.error(String(e)),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(prefs);
  }

  return (
    <form onSubmit={submit} className="grid max-w-xl gap-4 sm:grid-cols-2">
      <div className="space-y-1.5">
        <Label htmlFor="locale">{t("language")}</Label>
        <Select
          value={prefs.locale}
          onValueChange={(v) => setPrefs({ ...prefs, locale: v as "fr" | "en" })}
        >
          <SelectTrigger id="locale" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="fr">{tl("fr")}</SelectItem>
            <SelectItem value="en">{tl("en")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="tz">{t("timezone")}</Label>
        <Input
          id="tz"
          value={prefs.timezone}
          onChange={(e) => setPrefs({ ...prefs, timezone: e.target.value })}
        />
      </div>
      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor="cap">{t("sendCap")}</Label>
        <Input
          id="cap"
          type="number"
          min={1}
          max={200}
          className="w-28"
          value={prefs.daily_send_cap}
          onChange={(e) => setPrefs({ ...prefs, daily_send_cap: Number(e.target.value) })}
          aria-describedby="cap-hint"
        />
        <p id="cap-hint" className="text-xs text-muted-foreground">
          {t("sendCapHint")}
        </p>
      </div>
      <div className="flex items-start gap-2 sm:col-span-2">
        <Checkbox
          id="sending"
          checked={prefs.sending_enabled ?? false}
          onCheckedChange={(v) => setPrefs({ ...prefs, sending_enabled: v === true })}
          aria-describedby="sending-hint"
          className="mt-0.5"
        />
        <div className="space-y-0.5">
          <Label htmlFor="sending">{t("sending")}</Label>
          <p id="sending-hint" className="text-xs text-muted-foreground">
            {t("sendingHint")}
          </p>
        </div>
      </div>
      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor="signature">{t("signature")}</Label>
        <Textarea
          id="signature"
          rows={4}
          maxLength={1000}
          value={prefs.email_signature ?? ""}
          onChange={(e) => setPrefs({ ...prefs, email_signature: e.target.value })}
          aria-describedby="signature-hint"
        />
        <p id="signature-hint" className="text-xs text-muted-foreground">
          {t("signatureHint")}
        </p>
      </div>
      <div className="sm:col-span-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? tc("saving") : tc("save")}
        </Button>
      </div>
    </form>
  );
}

function Details({ s }: { s: SettingsOut }) {
  const t = useTranslations("settings");
  const tp = useTranslations("settings.purposes");
  const triage = s.gmail.find((g) => g.scope_set === "triage");
  const send = s.gmail.find((g) => g.scope_set === "send");
  const locale = useLocale();
  const nf = new Intl.NumberFormat(locale);
  return (
    <>
      <Section title={t("gmail")}>
        <dl className="divide-y border-y">
          <Row label={t("scopeTriage")}>
            {triage ? (
              <Ok ok>{t("gmailConnected", { email: triage.account_email ?? "?" })}</Ok>
            ) : (
              <Ok ok={false}>{t("gmailNotConnected")}</Ok>
            )}
          </Row>
          <Row label={t("scopeSend")}>
            {send ? (
              <Ok ok>{send.account_email}</Ok>
            ) : (
              <span className="text-muted-foreground">{t("sendNotConnected")}</span>
            )}
          </Row>
        </dl>
      </Section>

      <Section title={t("llm")}>
        <p className="mb-2 text-sm text-muted-foreground">{t("llmHint")}</p>
        <dl className="divide-y border-y">
          <Row label={t("provider")}>
            <span className="capitalize">{s.llm.provider}</span>{" "}
            <Ok ok={s.llm.provider === "groq" ? s.llm.groq_key_set : s.llm.openai_key_set}>
              {(s.llm.provider === "groq" ? s.llm.groq_key_set : s.llm.openai_key_set)
                ? t("keySet")
                : t("keyMissing")}
            </Ok>
          </Row>
          {Object.entries(s.llm.models).map(([purpose, model]) => (
            <Row key={purpose} label={tp(purpose as Purpose)}>
              {model}
            </Row>
          ))}
          <Row label="">
            <span className="text-muted-foreground">
              {t("limits", {
                tpm: nf.format(s.llm.limits.tokens_per_minute ?? 0),
                rpm: nf.format(s.llm.limits.requests_per_minute ?? 0),
                tpd: nf.format(s.llm.limits.tokens_per_day ?? 0),
              })}
            </span>
          </Row>
        </dl>
      </Section>

      <Section title={t("triage")}>
        <dl className="divide-y border-y">
          <Row label={t("schedule")}>{t("scheduleValue")}</Row>
          <Row label={t("query")}>{String(s.triage.gmail_query)}</Row>
          <Row label={t("batchSize")}>{String(s.triage.batch_size)}</Row>
          <Row label={t("autoApply")}>
            {t("autoApplyValue", { value: String(s.triage.auto_apply_confidence) })}
          </Row>
        </dl>
      </Section>

      <Section title={t("sources")}>
        <p className="text-sm text-muted-foreground">{t("sourcesSoon")}</p>
      </Section>
    </>
  );
}

export default function SettingsPage() {
  const t = useTranslations("settings");
  const tc = useTranslations("common");
  const ts = useTranslations("status");
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => unwrap(api.GET("/api/v1/settings")),
  });
  return (
    <>
      <PageHeader title={t("title")} />
      {settings.isError ? (
        <ErrorState
          message={tc("loadError")}
          onRetry={() => settings.refetch()}
          retryLabel={tc("retry")}
        />
      ) : !settings.data ? (
        <Skeleton className="h-64 w-full" aria-busy="true" />
      ) : (
        <div className="space-y-10">
          <Section title={t("preferences")}>
            <PreferencesForm initial={settings.data.preferences} />
          </Section>
          <Details s={settings.data} />
          <Section title={ts("title")}>
            <SystemStatus />
          </Section>
        </div>
      )}
    </>
  );
}
