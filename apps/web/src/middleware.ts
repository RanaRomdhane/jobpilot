import { NextResponse, type NextRequest } from "next/server";

// Cheap gate: no session cookie → login page. The API validates the session on every request.
export function middleware(request: NextRequest) {
  if (!request.cookies.get("jp_session")) {
    const login = new URL("/login", request.url);
    if (request.nextUrl.pathname !== "/") login.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!login|api|healthz|_next|favicon.ico).*)"],
};
