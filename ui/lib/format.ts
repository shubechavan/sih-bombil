/** Format a number with commas: 12345 → "12,345" */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

/** Format as percentage: 0.875 → "87.5%" */
export function formatPercent(n: number, decimals = 1): string {
  return `${(n * 100).toFixed(decimals)}%`;
}

/** Format risk score: 0.854 → "0.854" */
export function formatScore(n: number): string {
  if (Number.isNaN(n) || n == null) return "—";
  return n.toFixed(3);
}

/** Truncate hash: "a1b2c3d4e5f6" → "a1b2c3d4" */
export function truncHash(hash: string, length = 8): string {
  return hash.slice(0, length);
}

/** Truncate text with ellipsis */
export function truncText(text: string, maxLen = 120): string {
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen)}…`;
}

/** Relative time: "2 min ago", "1 hr ago" */
export function relativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;

  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/** Format duration in ms → "1.2s" */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
