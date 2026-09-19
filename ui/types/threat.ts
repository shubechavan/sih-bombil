/**
 * The severity vocabulary the tactical components are styled against.
 *
 * All that survives of v1's threat model. `StatCard` and `lib/severity.ts`
 * take a Severity to pick a colour token; attribution confidence bands map
 * onto these in `lib/attribution.ts` (BAND_TOKEN).
 */
export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
