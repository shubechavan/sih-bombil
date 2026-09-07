import { type NextRequest, NextResponse } from "next/server";
import { backendUrl } from "../_backend";

export async function GET(req: NextRequest) {
	try {
		const query = req.nextUrl.searchParams.toString();
		const url = `${backendUrl("/threats")}${query ? `?${query}` : ""}`;

		const res = await fetch(url, { cache: "no-store" });
		if (!res.ok) {
			const text = await res.text();
			return NextResponse.json({ error: text }, { status: res.status });
		}

		const data = await res.json();
		return NextResponse.json(data);
	} catch (err) {
		return NextResponse.json(
			{ error: err instanceof Error ? err.message : "Failed to fetch threats" },
			{ status: 502 },
		);
	}
}
