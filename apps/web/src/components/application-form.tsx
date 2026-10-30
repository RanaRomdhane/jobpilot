"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { STATUSES, api, unwrap, type ApplicationStatus } from "@/lib/api/client";

const schema = z.object({
  company_name: z.string().trim().min(1),
  company_domain: z.string().trim().max(255).optional(),
  role_title: z.string().trim().min(1),
  status: z.enum(STATUSES),
  applied_at: z.string().optional(),
  contact_email: z.union([z.literal(""), z.email()]).optional(),
  apply_url: z.union([z.literal(""), z.url()]).optional(),
  notes: z.string().max(5000).optional(),
});
type FormValues = z.infer<typeof schema>;

function Field({
  id,
  label,
  error,
  hint,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function NewApplicationDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("applications.form");
  const ts = useTranslations("statuses");
  const tc = useTranslations("common");
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ApplicationStatus>("sent");
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: "onBlur",
    defaultValues: { status: "sent", applied_at: new Date().toISOString().slice(0, 10) },
  });
  const errors = form.formState.errors;
  const errorText = (key: keyof FormValues) => {
    const e = errors[key];
    if (!e) return undefined;
    return key === "contact_email"
      ? t("invalidEmail")
      : e.type === "too_small"
        ? t("required")
        : String(e.message);
  };

  const create = useMutation({
    mutationFn: (v: FormValues) =>
      unwrap(
        api.POST("/api/v1/applications", {
          body: {
            company_name: v.company_name,
            company_domain: v.company_domain || null,
            role_title: v.role_title,
            status: v.status,
            applied_at: v.applied_at ? new Date(v.applied_at).toISOString() : null,
            contact_email: v.contact_email || null,
            apply_url: v.apply_url || null,
            notes: v.notes || null,
            apply_channel: "email",
          },
        }),
      ),
    onSuccess: () => {
      toast.success(t("created"));
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      form.reset();
      setStatus("sent");
      onOpenChange(false);
    },
    onError: (e) => toast.error(String(e)),
  });

  const aria = (key: keyof FormValues) => ({
    "aria-invalid": errors[key] ? true : undefined,
    "aria-describedby": errors[key] ? `${key}-error` : undefined,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription className="sr-only">{t("title")}</DialogDescription>
        </DialogHeader>
        <form
          id="new-application"
          onSubmit={form.handleSubmit((v) => create.mutate(v))}
          className="grid gap-4 sm:grid-cols-2"
          noValidate
        >
          <Field id="company_name" label={t("company")} error={errorText("company_name")}>
            <Input
              id="company_name"
              autoFocus
              {...form.register("company_name")}
              {...aria("company_name")}
            />
          </Field>
          <Field id="role_title" label={t("role")} error={errorText("role_title")}>
            <Input id="role_title" {...form.register("role_title")} {...aria("role_title")} />
          </Field>
          <div className="sm:col-span-2">
            <Field id="company_domain" label={t("domain")} hint={t("domainHint")}>
              <Input
                id="company_domain"
                placeholder="acme.tn"
                {...form.register("company_domain")}
                aria-describedby="company_domain-hint"
              />
            </Field>
          </div>
          <Field id="status" label={t("status")}>
            <Select
              value={status}
              onValueChange={(v) => {
                setStatus(v as ApplicationStatus);
                form.setValue("status", v as ApplicationStatus);
              }}
            >
              <SelectTrigger id="status" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {ts(s)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field id="applied_at" label={t("appliedAt")}>
            <Input id="applied_at" type="date" {...form.register("applied_at")} />
          </Field>
          <Field id="contact_email" label={t("contact")} error={errorText("contact_email")}>
            <Input
              id="contact_email"
              type="email"
              autoComplete="off"
              {...form.register("contact_email")}
              {...aria("contact_email")}
            />
          </Field>
          <Field id="apply_url" label={t("url")} error={errors.apply_url ? "URL" : undefined}>
            <Input
              id="apply_url"
              type="url"
              placeholder="https://"
              {...form.register("apply_url")}
              {...aria("apply_url")}
            />
          </Field>
          <div className="sm:col-span-2">
            <Field id="notes" label={t("notes")}>
              <Textarea id="notes" rows={3} {...form.register("notes")} />
            </Field>
          </div>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tc("cancel")}
          </Button>
          <Button type="submit" form="new-application" disabled={create.isPending}>
            {create.isPending ? tc("saving") : t("create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
