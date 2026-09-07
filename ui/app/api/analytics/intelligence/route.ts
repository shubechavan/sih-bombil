import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export async function GET() {
	try {
		const res = await fetch(backendUrl("/analytics/intelligence"));

		if (!res.ok) {
			const text = await res.text();
			return NextResponse.json({ error: text }, { status: res.status });
		}

		const data = await res.json();
		return NextResponse.json(data);
	} catch (err) {
		return NextResponse.json(
			{ error: err instanceof Error ? err.message : "Intelligence analytics failed" },
			{ status: 502 },
		);
	}
}
