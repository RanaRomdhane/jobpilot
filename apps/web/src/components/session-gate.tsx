"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { api, setCsrfToken, unwrap } from "@/lib/api/client";

/** Loads the session (and its CSRF token) before rendering the app; sends you to login if needed. */
export function SessionGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => unwrap(api.GET("/api/v1/auth/session")),
    staleTime: Infinity,
  });

  useEffect(() => {
    if (session.data?.authenticated) setCsrfToken(session.data.csrf_token ?? null);
    else if (session.data) router.replace(`/login?next=${encodeURIComponent(location.pathname)}`);
  }, [session.data, router]);

  if (!session.data?.authenticated) {
    return (
      <div className="p-6" aria-busy="true">
        <Skeleton className="h-8 w-48" />
      </div>
    );
  }
  return <>{children}</>;
}
