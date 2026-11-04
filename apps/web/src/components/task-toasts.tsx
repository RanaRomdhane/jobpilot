"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { toast } from "sonner";

import type { TaskEvent } from "@/lib/api/client";

const KNOWN = ["triage", "user_label_change", "ping", "parse_resume", "ats_report"] as const;

/** One SSE connection for the whole app: background-task results become toasts + fresh data. */
export function TaskToasts() {
  const t = useTranslations("tasks");
  const queryClient = useQueryClient();

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      source = new EventSource("/api/v1/tasks/stream");
      source.addEventListener("task", (message) => {
        const event = JSON.parse((message as MessageEvent<string>).data) as TaskEvent;
        if (event.status !== "succeeded" && event.status !== "failed") return;
        const kind = (KNOWN as readonly string[]).includes(event.kind)
          ? t(event.kind as (typeof KNOWN)[number])
          : event.kind;
        if (event.status === "succeeded") {
          // label syncs are confirmed where they were triggered; keep the noise down
          if (event.kind === "triage") {
            const r = (event.result ?? {}) as { classified?: number; flagged?: number };
            toast.success(t("succeeded", { kind }), {
              description: t("triageSummary", {
                classified: r.classified ?? 0,
                flagged: r.flagged ?? 0,
              }),
            });
          } else if (event.kind === "parse_resume" || event.kind === "ats_report") {
            const r = (event.result ?? {}) as { score?: number };
            toast.success(t("succeeded", { kind }), {
              description: r.score !== undefined ? t("atsSummary", { score: r.score }) : undefined,
            });
          } else if (event.kind !== "user_label_change") {
            toast.success(t("succeeded", { kind }));
          }
        } else {
          toast.error(t("failed", { kind, error: event.error ?? "?" }));
        }
        void queryClient.invalidateQueries();
      });
      source.onerror = () => {
        source?.close();
        retry = setTimeout(connect, 10_000);
      };
    };
    connect();
    return () => {
      source?.close();
      clearTimeout(retry);
    };
  }, [queryClient, t]);

  return null;
}
