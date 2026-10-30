import { getTranslations } from "next-intl/server";

import { SystemStatus } from "@/components/system-status";

export default async function OverviewPage() {
  const t = await getTranslations("status");
  return (
    <section aria-labelledby="status-title" className="space-y-6">
      <div className="space-y-1">
        <h1 id="status-title" className="text-2xl font-semibold tracking-tight">
          {t("title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("description")}</p>
      </div>
      <SystemStatus />
    </section>
  );
}
