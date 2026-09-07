export function getBackendBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL?.trim() || process.env.BACKEND_URL?.trim();
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
