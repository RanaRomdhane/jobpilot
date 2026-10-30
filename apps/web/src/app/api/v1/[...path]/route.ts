/**
 * Server-side proxy ("backend for frontend") to the FastAPI service.
 *
 * The browser only ever talks to this Next.js origin; the API is reached on the internal Docker
 * network. Dashboard authentication (Phase 3) is enforced here before anything is forwarded.
 * Responses are streamed through untouched, so SSE task progress works.
 */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const API_URL = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";

// Hop-by-hop headers must not be forwarded (RFC 9110 §7.6.1); cookies stay at the edge.
const DROP_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "proxy-authorization",
  "te",
  "trailer",
  "cookie",
  "content-length",
]);
const DROP_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-encoding",
  "content-length",
]);

const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/;

async function proxy(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await params;
  if (!path.every((s) => SAFE_SEGMENT.test(s) && s !== ".." && s !== ".")) {
    return Response.json({ detail: "invalid path" }, { status: 400 });
  }
  const target = new URL(`/api/v1/${path.join("/")}`, API_URL);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!DROP_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });

  const hasBody = !["GET", "HEAD"].includes(request.method);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      redirect: "manual",
      cache: "no-store",
      signal: request.signal,
      // required by Node's fetch when streaming a request body
      ...(hasBody ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch {
    return Response.json({ detail: "api unreachable" }, { status: 502 });
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!DROP_RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
  });
  if (responseHeaders.get("content-type")?.startsWith("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { proxy as DELETE, proxy as GET, proxy as PATCH, proxy as POST, proxy as PUT };
