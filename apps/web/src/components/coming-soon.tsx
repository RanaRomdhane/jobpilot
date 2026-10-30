import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/page-header";

type Key = "jobs" | "resume" | "templates" | "review";

export async function ComingSoon({ page }: { page: Key }) {
  const t = await getTranslations("soon");
  const nav = await getTranslations("nav");
  return (
    <>
      <PageHeader title={nav(page)} />
      <div className="rounded-lg border border-dashed p-8">
        <p className="font-medium">{t("title")}</p>
        <p className="mt-1 text-sm text-muted-foreground">{t(page)}</p>
      </div>
    </>
  );
}
