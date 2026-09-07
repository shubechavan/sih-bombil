import { NextResponse } from "next/server";
import { backendUrl } from "../_backend";

export async function GET() {
	try {
		const res = await fetch(backendUrl("/health"), {
			next: { revalidate: 0 },
		});

		if (!res.ok) {
			return NextResponse.json(
				{ error: "Backend health check failed" },
				{ status: res.status },
			);
		}

		const data = await res.json();
		return NextResponse.json(data);
	} catch (err) {
		return NextResponse.json(
			{ error: err instanceof Error ? err.message : "Backend health fetch failed" },
			{ status: 502 },
		);
	}
}
