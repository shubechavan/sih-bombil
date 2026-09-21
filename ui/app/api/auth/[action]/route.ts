import { type NextRequest, NextResponse } from "next/server";
import { backendUrl } from "../../_backend";
import { SESSION_COOKIE, clearSession, setSession } from "../../_session";

/**
 * Login and logout. The only place the token is ever handled.
 *
 * The browser never sees it. This handler posts the credentials to FastAPI,
 * takes the JWT out of the response and puts it in an httpOnly cookie, then
 * returns only the username and role to the page. No script on the page can
 * read the token, which is the whole reason the cookie is set here rather than
 * the client storing what FastAPI returned.
 */

export async function POST(request: NextRequest, context: { params: { action: string } }) {
	const action = context.params.action;

	if (action === "logout") {
		const response = NextResponse.json({ ok: true });
		clearSession(response);
		return response;
	}

	if (action !== "login") {
		return NextResponse.json({ detail: `Unknown auth action ${action}` }, { status: 404 });
	}

	try {
		const upstream = await fetch(backendUrl("/auth/login"), {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: await request.text(),
			cache: "no-store",
		});
		const payload = await upstream.json();

		if (!upstream.ok) {
			return NextResponse.json(payload, { status: upstream.status });
		}

		// Everything but the token goes back to the page.
		const { access_token, expires_at, ...identity } = payload;
		const response = NextResponse.json(identity);
		setSession(response, access_token, expires_at);
		return response;
	} catch (err) {
		return NextResponse.json(
			{
				detail:
					err instanceof Error
						? `Cannot reach the attribution API: ${err.message}`
						: "Cannot reach the attribution API",
				hint: "Start it with: uvicorn api.main:app --reload --port 8000",
			},
			{ status: 502 },
		);
	}
}

/** Who is signed in, according to the cookie. Used to gate the app shell. */
export async function GET(request: NextRequest, context: { params: { action: string } }) {
	if (context.params.action !== "me") {
		return NextResponse.json({ detail: "Unknown auth action" }, { status: 404 });
	}

	const token = request.cookies.get(SESSION_COOKIE)?.value;
	if (!token) {
		return NextResponse.json({ detail: "not signed in" }, { status: 401 });
	}

	try {
		const upstream = await fetch(backendUrl("/auth/me"), {
			headers: { authorization: `Bearer ${token}` },
			cache: "no-store",
		});
		const payload = await upstream.json();
		const response = NextResponse.json(payload, { status: upstream.status });
		// An expired or rejected token is a dead cookie; drop it so the UI does
		// not keep presenting a session that no longer exists.
		if (upstream.status === 401) clearSession(response);
		return response;
	} catch {
		return NextResponse.json({ detail: "Cannot reach the attribution API" }, { status: 502 });
	}
}
