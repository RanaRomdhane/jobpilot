"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Health } from "@/lib/api/client";
import { followTask } from "@/lib/api/task-stream";

const COMPONENTS = ["database", "redis", "worker", "latex"] as const;

async function fetchHealth(): Promise<Health> {
  // 503 still carries a HealthResponse body ("down"), so read data or error alike
  const { data, error, response } = await api.GET("/api/v1/health");
  const body = (data ?? error) as Health | undefined;
  if (!body || !("components" in body)) throw new Error(`health ${response.status}`);
  return body;
}

export function SystemStatus() {
  const t = useTranslations("status");
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 15_000 });
  const [pingProgress, setPingProgress] = useState<number | null>(null);

  async function runPing() {
    const { data } = await api.POST("/api/v1/tasks/ping");
    if (!data) {
      toast.error(t("pingFailed"));
      return;
    }
    setPingProgress(0);
    followTask(
      data.task_id,
      (event) => {
        setPingProgress(Math.round(event.progress * 100));
        if (event.status === "succeeded") {
          toast.success(t("pingDone"));
          setPingProgress(null);
        } else if (event.status === "failed") {
          toast.error(t("pingFailed"));
          setPingProgress(null);
        }
      },
      () => setPingProgress(null),
    );
  }

  if (health.isPending) {
    return (
      <div className="grid gap-4 sm:grid-cols-2" aria-busy="true">
        {COMPONENTS.map((c) => (
          <Skeleton key={c} className="h-24 rounded-xl" />
        ))}
      </div>
    );
  }

  if (health.isError) {
    return (
      <Card role="alert">
        <CardContent className="flex flex-col items-start gap-3 pt-6 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm">{t("unreachable")}</p>
          <Button variant="outline" onClick={() => health.refetch()}>
            <RefreshCw aria-hidden className="size-4" />
            {t("retry")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  const { status, components, version } = health.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2" role="status" aria-live="polite">
          <Badge variant={status === "ok" ? "default" : "destructive"}>
            {t(`overall.${status}`)}
          </Badge>
          <span className="text-xs text-muted-foreground">v{version}</span>
        </div>
        <Button onClick={runPing} disabled={pingProgress !== null}>
          {pingProgress === null ? t("runPing") : t("pingRunning", { progress: pingProgress })}
        </Button>
      </div>
      <ul className="grid gap-4 sm:grid-cols-2">
        {components.map((c) => (
          <li key={c.name}>
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  {c.ok ? (
                    <CircleCheck
                      aria-hidden
                      className="size-4 text-emerald-600 dark:text-emerald-400"
                    />
                  ) : (
                    <CircleAlert aria-hidden className="size-4 text-destructive" />
                  )}
                  {(COMPONENTS as readonly string[]).includes(c.name)
                    ? t(`components.${c.name as (typeof COMPONENTS)[number]}`)
                    : c.name}
                  <span className="sr-only">: {c.ok ? t("ok") : t("ko")}</span>
                </CardTitle>
                <CardDescription className="font-mono text-xs break-words">
                  {c.detail ?? (c.ok ? t("ok") : t("ko"))}
                  {c.latency_ms != null ? ` · ${c.latency_ms} ms` : ""}
                </CardDescription>
              </CardHeader>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}
