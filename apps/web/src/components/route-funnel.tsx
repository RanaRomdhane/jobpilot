"use client";

import { useLocale, useTranslations } from "next-intl";

import type { Overview } from "@/lib/api/client";
import { percent } from "@/lib/format";
import { cn } from "@/lib/utils";

type Step = Overview["funnel"][number];
type StopKey = "sent" | "recruiter_contact" | "interview" | "offer";

/**
 * The pipeline drawn as a route with four stops. Flown legs are solid, the rest dashed;
 * each leg shows its conversion rate. Semantic markup underneath: an ordered list.
 */
export function RouteFunnel({ steps }: { steps: Step[] }) {
  const t = useTranslations("overview.route");
  const locale = useLocale();
  const total = steps[0]?.count ?? 0;

  if (total === 0) {
    return <p className="text-sm text-muted-foreground">{t("empty")}</p>;
  }

  return (
    <ol className="grid grid-cols-4">
      {steps.map((step, i) => {
        const reached = step.count > 0;
        const next = steps[i + 1];
        const conversion = next && step.count > 0 ? next.count / step.count : null;
        const nextReached = next ? next.count > 0 : false;
        return (
          <li key={step.status} className="relative flex flex-col items-start">
            {/* the leg towards the next stop */}
            {next ? (
              <div aria-hidden className="absolute top-[2.85rem] right-0 left-3 flex items-center">
                <div
                  className={cn(
                    "h-0 w-full border-t-2",
                    nextReached ? "border-primary" : "border-dashed border-muted-foreground/40",
                  )}
                />
              </div>
            ) : null}
            <span
              className={cn(
                "text-3xl font-semibold tracking-[-0.02em] tabular-nums sm:text-4xl",
                !reached && "text-muted-foreground/50",
              )}
            >
              {step.count}
            </span>
            <span
              aria-hidden
              className={cn(
                "relative z-10 my-1.5 size-6 rounded-full border-2",
                reached
                  ? "border-primary bg-primary shadow-[0_0_0_4px_var(--background)]"
                  : "border-muted-foreground/40 bg-background shadow-[0_0_0_4px_var(--background)]",
              )}
            />
            <span className="text-sm font-medium">{t(step.status as StopKey)}</span>
            {conversion !== null ? (
              <span className="absolute top-[3.5rem] left-1/2 hidden text-xs text-muted-foreground tabular-nums sm:block">
                {percent(conversion, locale)}
              </span>
            ) : null}
            <span className="sr-only">
              {conversion !== null
                ? ` (${percent(conversion, locale)} → ${t((next?.status ?? "offer") as StopKey)})`
                : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
