import type { NextRequest } from "next/server";
import { backendUrl } from "../../_backend";

// Force dynamic rendering so the SSE stream is never statically cached
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
	const scanId = req.nextUrl.searchParams.get("scanId");
	if (!scanId) {
		return new Response(JSON.stringify({ error: "scanId required" }), {
			status: 400,
			headers: { "Content-Type": "application/json" },
		});
	}

	try {
		const upstream = await fetch(`${backendUrl("/scan/status")}?scanId=${encodeURIComponent(scanId)}`, {
			headers: { Accept: "text/event-stream" },
		});

		if (!upstream.ok || !upstream.body) {
			return new Response(JSON.stringify({ error: "Upstream SSE failed" }), {
				status: upstream.status,
				headers: { "Content-Type": "application/json" },
			});
		}

		// Stream SSE through to the client
		return new Response(upstream.body, {
			status: 200,
			headers: {
				"Content-Type": "text/event-stream",
				"Cache-Control": "no-cache, no-transform",
				Connection: "keep-alive",
			},
		});
	} catch {
		return new Response(JSON.stringify({ error: "SSE connection failed" }), {
			status: 502,
			headers: { "Content-Type": "application/json" },
		});
	}
}
