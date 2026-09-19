import ky from "ky";

const DEV_BACKEND_FALLBACK = "http://localhost:8000";

function resolveApiBase(): string {
	const envBase = process.env.NEXT_PUBLIC_API_URL?.trim();
	const resolved = (envBase || DEV_BACKEND_FALLBACK).replace(/\/+$/, "");
	return resolved;
}

const API_BASE = resolveApiBase();

/**
 * Direct backend client — use ONLY in server-side code (route handlers, server components).
 * Client components must use `bff` to avoid CORS errors.
 */
export const api = ky.create({
	prefixUrl: API_BASE,
	timeout: 30000,
	retry: {
		limit: 2,
		methods: ["get"],
		statusCodes: [408, 502, 503, 504],
	},
	hooks: {
		beforeError: [
			(error) => {
				const { response } = error;
				if (response) {
					error.message = `API ${response.status}: ${response.statusText}`;
				}
				return error;
			},
		],
	},
});

/** Internal BFF fetch (same origin) */
export const bff = ky.create({
	prefixUrl: "/api",
	timeout: 60000,
	retry: {
		limit: 1,
		methods: ["get"],
		statusCodes: [502, 503, 504],
	},
});
