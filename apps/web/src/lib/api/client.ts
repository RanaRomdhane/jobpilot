import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Health = Schemas["HealthResponse"];
export type TaskEvent = Schemas["TaskEvent"];
export type Overview = Schemas["Overview"];
export type Email = Schemas["EmailOut"];
export type EmailDetail = Schemas["EmailDetail"];
export type EmailPage = Schemas["EmailPage"];
export type Category = NonNullable<Email["category"]>;
export type Application = Schemas["ApplicationOut"];
export type ApplicationDetail = Schemas["ApplicationDetail"];
export type ApplicationStatus = Application["status"];
export type Suggestion = Schemas["SuggestionOut"];
export type SecurityReport = Schemas["SecurityReport"];
export type SettingsOut = Schemas["SettingsOut"];
export type Preferences = Schemas["Preferences"];

export const CATEGORIES = [
  "Recruteur",
  "Entretien",
  "Refus",
  "Offre",
  "Newsletter",
  "Autre",
] as const satisfies readonly Category[];

export const STATUSES = [
  "draft",
  "ready_for_review",
  "sent",
  "recruiter_contact",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
  "ghosted",
] as const satisfies readonly ApplicationStatus[];

let csrfToken: string | null = null;
export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail);
  }
}

const auth: Middleware = {
  onRequest({ request }) {
    if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      request.headers.set("X-CSRF-Token", csrfToken);
    }
    return request;
  },
  onResponse({ response, request }) {
    const isSessionCall = new URL(request.url).pathname.startsWith("/api/v1/auth/");
    if (response.status === 401 && !isSessionCall && typeof window !== "undefined") {
      const next = encodeURIComponent(window.location.pathname);
      window.location.assign(`/login?next=${next}`);
    }
    return response;
  },
};

/** Typed client generated from the FastAPI OpenAPI schema (`make gen-client`). */
export const api = createClient<paths>({ baseUrl: "" });
api.use(auth);

/** Unwrap an openapi-fetch result or throw an ApiError with the API's `detail`. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise;
  if (data !== undefined && response.ok) return data;
  const detail =
    error && typeof error === "object" && "detail" in error
      ? String((error as { detail: unknown }).detail)
      : response.statusText;
  throw new ApiError(response.status, detail);
}
