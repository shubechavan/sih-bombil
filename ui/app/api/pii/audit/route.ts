import { type NextRequest, NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export async function GET(req: NextRequest) {
	try {
		const { searchParams } = new URL(req.url);
		const limit = searchParams.get("limit") ?? "100";

		const res = await fetch(backendUrl(`/pii/audit?limit=${limit}`), {
			cache: "no-store",
		});

		if (!res.ok) {
			const text = await res.text();
			return NextResponse.json({ error: text }, { status: res.status });
		}

		const data = await res.json();
		return NextResponse.json(data);
	} catch (err) {
		return NextResponse.json(
			{ error: err instanceof Error ? err.message : "PII audit fetch failed" },
			{ status: 502 },
		);
	}
}
