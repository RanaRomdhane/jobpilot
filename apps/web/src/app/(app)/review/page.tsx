import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { PageHeader } from "@/components/page-header";
import { ReviewScreen } from "@/components/review/review-screen";
import { Skeleton } from "@/components/ui/skeleton";

export default async function Page() {
  const t = await getTranslations("review");
  const nav = await getTranslations("nav");
  return (
    <>
      <PageHeader title={nav("review")} description={t("intro")} />
      <Suspense fallback={<Skeleton className="h-96" />}>
        <ReviewScreen />
      </Suspense>
    </>
  );
}
