import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

/** Typed client generated from the FastAPI OpenAPI schema (`make gen-client`). */
export const api = createClient<paths>({ baseUrl: "" });

export type Health = components["schemas"]["HealthResponse"];
export type TaskEvent = components["schemas"]["TaskEvent"];

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
