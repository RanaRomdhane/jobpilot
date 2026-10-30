"use client";

import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { Button } from "@/components/ui/button";
import { locales, type Locale } from "@/i18n/config";
import { setLocale } from "@/i18n/actions";

export function LocaleSwitcher() {
  const t = useTranslations("locale");
  const current = useLocale() as Locale;
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  return (
    <div role="group" aria-label={t("label")} className="flex items-center gap-1">
      {locales.map((locale) => (
        <Button
          key={locale}
          variant={locale === current ? "secondary" : "ghost"}
          size="sm"
          aria-pressed={locale === current}
          disabled={pending}
          lang={locale}
          title={t(locale)}
          onClick={() =>
            startTransition(async () => {
              await setLocale(locale);
              router.refresh();
            })
          }
        >
          {locale.toUpperCase()}
        </Button>
      ))}
    </div>
  );
}
