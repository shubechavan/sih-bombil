import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export async function POST() {
	try {
		const res = await fetch(backendUrl("/alert/test"), {
			method: "POST",
			headers: { "Content-Type": "application/json" },
		});

		if (!res.ok) {
			const text = await res.text();
			return NextResponse.json({ error: text }, { status: res.status });
		}

		const data = await res.json();
		return NextResponse.json(data);
	} catch (err) {
		return NextResponse.json(
			{ error: err instanceof Error ? err.message : "Webhook test failed" },
			{ status: 502 },
		);
	}
}
