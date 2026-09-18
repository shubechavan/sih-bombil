import { type NextRequest, NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

/**
 * Catch-all proxy to the FastAPI attribution API.
 *
 * One handler rather than seven, because every attribution endpoint is a plain
 * read that needs the same treatment: forward the query string, never cache,
 * and pass the backend's own error detail through so a 404 on an actor reads as
 * "no actor 7" in the UI instead of a generic failure.
 *
 * Export responses are streamed back with their Content-Disposition intact so
 * the browser downloads a file rather than rendering CSV as text.
 */

const ALLOWED = ["actors", "graph", "timeline", "recon", "scan", "export", "health", "meta"];

export async function GET(request: NextRequest, context: { params: { path: string[] } }) {
	const segments = context.params.path ?? [];
	if (segments.length === 0 || !ALLOWED.includes(segments[0])) {
		return NextResponse.json(
			{ detail: `Unknown endpoint /${segments.join("/")}` },
			{ status: 404 },
		);
	}

	const target = `/${segments.map(encodeURIComponent).join("/")}`;
	const search = request.nextUrl.search;

	try {
		const res = await fetch(backendUrl(`${target}${search}`), { cache: "no-store" });
		const contentType = res.headers.get("content-type") ?? "";

		if (contentType.includes("application/json")) {
			const body = await res.json();
			return NextResponse.json(body, { status: res.status });
		}

		// csv, or anything else the backend chose to send as a file
		const body = await res.text();
		const headers = new Headers({ "content-type": contentType || "text/plain" });
		const disposition = res.headers.get("content-disposition");
		if (disposition) headers.set("content-disposition", disposition);
		return new NextResponse(body, { status: res.status, headers });
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

export async function POST(request: NextRequest, context: { params: { path: string[] } }) {
	const segments = context.params.path ?? [];
	if (segments[0] !== "scan") {
		return NextResponse.json({ detail: "Only /scan accepts POST" }, { status: 405 });
	}

	try {
		const res = await fetch(backendUrl(`/${segments.map(encodeURIComponent).join("/")}`), {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: await request.text(),
			cache: "no-store",
		});
		return NextResponse.json(await res.json(), { status: res.status });
	} catch (err) {
		return NextResponse.json(
			{ detail: err instanceof Error ? err.message : "Scan request failed" },
			{ status: 502 },
		);
	}
}
