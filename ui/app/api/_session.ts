import type { NextRequest, NextResponse } from "next/server";

/**
 * The session cookie, in one place.
 *
 * httpOnly so no script on the page can read the token, sameSite=lax so it is
 * not sent on cross-site requests, and secure whenever we are not on localhost.
 * The cookie exists only so the route handlers can attach an Authorization
 * header server-side — it is never sent to FastAPI as a cookie, and FastAPI
 * does not look for one.
 */

export const SESSION_COOKIE = "ds_session";

export function setSession(response: NextResponse, token: string, expiresAt?: string) {
	// Match the token's own lifetime where the API told us, so the cookie and
	// the credential inside it expire together rather than leaving a cookie
	// that only fails once it is used.
	const expires = expiresAt ? new Date(expiresAt) : undefined;
	response.cookies.set({
		name: SESSION_COOKIE,
		value: token,
		httpOnly: true,
		sameSite: "lax",
		secure: process.env.NODE_ENV === "production",
		path: "/",
		...(expires && !Number.isNaN(expires.getTime()) ? { expires } : {}),
	});
}

export function clearSession(response: NextResponse) {
	response.cookies.set({
		name: SESSION_COOKIE,
		value: "",
		httpOnly: true,
		sameSite: "lax",
		secure: process.env.NODE_ENV === "production",
		path: "/",
		maxAge: 0,
	});
}

/** The bearer header for a proxied request, or an empty object when signed out. */
export function authHeader(request: NextRequest): Record<string, string> {
	const token = request.cookies.get(SESSION_COOKIE)?.value;
	return token ? { authorization: `Bearer ${token}` } : {};
}
