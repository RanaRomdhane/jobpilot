"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { LocaleSwitcher } from "@/components/locale-switcher";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api, setCsrfToken, unwrap } from "@/lib/api/client";

const ERRORS: Record<string, "invalid" | "tooMany" | "notSet"> = {
  invalid_password: "invalid",
  too_many_attempts: "tooMany",
  password_not_set: "notSet",
};

function LoginForm() {
  const t = useTranslations("login");
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const session = await unwrap(api.POST("/api/v1/auth/login", { body: { password } }));
      setCsrfToken(session.csrf_token ?? null);
      queryClient.setQueryData(["session"], session);
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (e) {
      const key = e instanceof ApiError ? ERRORS[e.detail] : undefined;
      setError(t(key ?? "error"));
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <div className="space-y-2">
        <Label htmlFor="password">{t("password")}</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          required
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setError(null);
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "login-error" : undefined}
        />
      </div>
      {error ? (
        <p id="login-error" role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button type="submit" className="w-full" disabled={pending || !password}>
        {pending ? t("submitting") : t("submit")}
      </Button>
    </form>
  );
}

export default function LoginPage() {
  const t = useTranslations("login");
  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 space-y-2">
          <svg viewBox="0 0 120 32" aria-hidden className="h-8 w-28 text-primary">
            <path
              d="M4 26 C 30 26, 34 6, 60 6 S 96 18, 116 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
            />
            <circle cx="4" cy="26" r="3.5" fill="currentColor" />
            <circle cx="60" cy="6" r="3.5" fill="currentColor" />
            <circle cx="116" cy="16" r="3.5" fill="currentColor" />
          </svg>
          <h1 className="text-2xl font-semibold tracking-[-0.015em]">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Suspense>
          <LoginForm />
        </Suspense>
        <div className="mt-10 flex items-center justify-between">
          <LocaleSwitcher />
          <ThemeToggle />
        </div>
      </div>
    </main>
  );
}
