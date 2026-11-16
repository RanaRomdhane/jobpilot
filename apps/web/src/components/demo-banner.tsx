"use client";

import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useTranslations } from "next-intl";

import { api, unwrap } from "@/lib/api/client";

/** Shown on every page of the demo stack (`make demo`), so invented data is never mistaken for yours. */
export function DemoBanner() {
  const t = useTranslations("demo");
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => unwrap(api.GET("/api/v1/settings")),
    staleTime: 5 * 60_000,
  });
  if (!settings.data?.demo) return null;
  return (
    <p
      role="status"
      className="mb-6 flex items-start gap-2 rounded-lg border border-dashed px-3 py-2 text-sm"
    >
      <FlaskConical aria-hidden className="mt-0.5 size-4 shrink-0 text-signal" />
      <span>
        <span className="font-medium">{t("title")}</span> {t("body")}
      </span>
    </p>
  );
}
