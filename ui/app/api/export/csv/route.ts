import type { NextRequest } from "next/server";
import { backendUrl } from "../../_backend";

export async function GET(req: NextRequest) {
	try {
		const query = req.nextUrl.searchParams.toString();
		const url = `${backendUrl("/threats/export/csv")}${query ? `?${query}` : ""}`;
		const res = await fetch(url, { cache: "no-store" });

		if (!res.ok) {
			const text = await res.text();
			return new Response(JSON.stringify({ error: text }), {
				status: res.status,
				headers: { "Content-Type": "application/json" },
			});
		}

		const csv = await res.text();

		return new Response(csv, {
			status: 200,
			headers: {
				"Content-Type": "text/csv; charset=utf-8",
				"Content-Disposition": `attachment; filename="darksentinel-threats-${Date.now()}.csv"`,
			},
		});
	} catch {
		return new Response(JSON.stringify({ error: "CSV export failed" }), {
			status: 502,
			headers: { "Content-Type": "application/json" },
		});
	}
}
