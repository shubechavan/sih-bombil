import { type NextRequest, NextResponse } from "next/server";
import { backendUrl } from "../_backend";

export async function POST(req: NextRequest) {
	try {
		const body = await req.json();

		const res = await fetch(backendUrl("/analyze"), {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});

		if (!res.ok) {
			const text = await res.text();
			return NextResponse.json({ error: text }, { status: res.status });
		}

		const data = await res.json();
		return NextResponse.json(data);
	} catch (err) {
		return NextResponse.json(
			{ error: err instanceof Error ? err.message : "Analysis failed" },
			{ status: 502 },
		);
	}
}
