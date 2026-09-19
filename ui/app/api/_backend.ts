export function getBackendBaseUrl(): string {
	// BACKEND_URL wins, and the order matters in a container. This module is only
	// ever imported by route handlers, which run on the server; NEXT_PUBLIC_* is
	// inlined at *build* time, so an image built for the browser's view of the API
	// ("http://localhost:8000") would otherwise make the server dial its own
	// container. BACKEND_URL is read at runtime and points at the compose service.
	const configured = process.env.BACKEND_URL?.trim() || process.env.NEXT_PUBLIC_API_URL?.trim();
	const fallback = process.env.NODE_ENV === "production" ? "" : "http://localhost:8000";
	const resolved = (configured || fallback).replace(/\/+$/, "");

	if (!resolved) {
		throw new Error("Backend URL is not configured. Set NEXT_PUBLIC_API_URL.");
	}

	return resolved;
}

export function backendUrl(pathname: string): string {
	const base = getBackendBaseUrl();
	const path = pathname.startsWith("/") ? pathname : `/${pathname}`;
	return `${base}${path}`;
}
