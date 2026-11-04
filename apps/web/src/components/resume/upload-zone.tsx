"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileUp, Lock } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRef, useState, type DragEvent } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ApiError, api, unwrap } from "@/lib/api/client";
import { cn } from "@/lib/utils";

export const MAX_UPLOAD_MB = 5;
const ACCEPT =
  ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export function useUploadResume(onStarted: (resumeId: number, taskId: string) => void) {
  const t = useTranslations("resume.upload");
  const locale = useLocale() as "fr" | "en";
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
        throw new ApiError(413, "upload_too_large");
      }
      return unwrap(
        api.POST("/api/v1/resume/uploads", {
          body: { file: file as unknown as string, locale },
          bodySerializer: (body) => {
            const data = new FormData();
            data.append("file", body.file as unknown as Blob);
            if (body.locale) data.append("locale", body.locale);
            return data;
          },
        }),
      );
    },
    onSuccess: async (r) => {
      toast.success(t("started"));
      await queryClient.invalidateQueries({ queryKey: ["resume", "uploads"] });
      onStarted(r.resume.id, r.task_id);
    },
    onError: (e: Error) => {
      const code = e instanceof ApiError ? e.detail.replace(/^upload_/, "") : "generic";
      const key = (
        ["too_large", "unsupported_type", "corrupt", "empty", "active_content"].includes(code)
          ? code
          : "generic"
      ) as "generic";
      toast.error(t(`errors.${key}`, { max: MAX_UPLOAD_MB }));
    },
  });
}

/** A file button that also accepts a dropped file (keyboard users get the button). */
export function UploadButton({
  onFile,
  pending,
  variant = "default",
}: {
  onFile: (file: File) => void;
  pending: boolean;
  variant?: "default" | "outline";
}) {
  const t = useTranslations("resume.upload");
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
      <Button variant={variant} disabled={pending} onClick={() => input.current?.click()}>
        <FileUp aria-hidden />
        {pending ? t("uploading") : t("button")}
      </Button>
    </>
  );
}

export function UploadZone({
  onFile,
  pending,
}: {
  onFile: (file: File) => void;
  pending: boolean;
}) {
  const t = useTranslations("resume.upload");
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) onFile(file);
  };
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
      className={cn(
        "flex flex-col items-center gap-4 rounded-lg border border-dashed px-6 py-12 text-center",
        over && "border-primary bg-accent",
      )}
    >
      <FileUp aria-hidden className="size-8 text-muted-foreground" />
      <div className="space-y-1">
        <p className="font-medium">{t("drop")}</p>
        <p className="text-sm text-muted-foreground">{t("hint", { max: MAX_UPLOAD_MB })}</p>
      </div>
      <UploadButton onFile={onFile} pending={pending} />
      <p className="flex max-w-md items-start gap-1.5 text-xs text-muted-foreground">
        <Lock aria-hidden className="mt-px size-3.5 shrink-0" />
        {t("privacy")}
      </p>
    </div>
  );
}
