/**
 * verify-pages.mjs — checks that the things this project refuses to guess at
 * actually reach the screen.
 *
 * The attribution pages fetch on the client, so the served HTML is an empty
 * shell and `curl` proves nothing. This drives a real browser and asserts on
 * the rendered DOM instead.
 *
 * What it checks is deliberately narrow: not that the pages look right, but
 * that an unmeasured component renders as its reason rather than a number, that
 * a persona stylometry refused says so, and that evidence appears in the words
 * the pipeline wrote. Those are the claims that would be quietly broken by a
 * component change, and the ones worth failing a build over.
 *
 *   node scripts/verify-pages.mjs [baseUrl]
 *
 * Exit codes: 0 everything passed, 1 an assertion failed, 75 no browser could
 * be launched. CI retries 75 and only 75 — see .github/workflows/ci.yml.
 */

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium } from "playwright-core";

const BASE = process.argv[2] ?? "http://localhost:3000";

//: The seeded demo admin. Overridable so this can run against a deployment
//: whose passwords are not the documented defaults.
const OPERATOR = process.env.VERIFY_OPERATOR ?? "admin";
const PASSWORD = process.env.VERIFY_PASSWORD ?? "admin-demo";
const CHANNELS = ["msedge", "chrome"];

const checks = [];
function check(name, ok, detail = "") {
	checks.push({ name, ok, detail });
	const mark = ok ? "  PASS" : "  FAIL";
	console.log(`${mark}  ${name}${detail && !ok ? ` — ${detail}` : ""}`);
}

/**
 * Exit code for "the browser would not start".
 *
 * Kept distinct from 1 so CI can retry a launch flake without retrying a failed
 * assertion. A suite that gets a second attempt at its own failures is not a
 * suite, and "just retry it" is how a real regression ends up merged.
 */
const E_NO_BROWSER = 75;

async function launch() {
	let lastError;
	for (const channel of CHANNELS) {
		try {
			return await chromium.launch({ channel, headless: true });
		} catch (err) {
			lastError = err;
		}
	}
	console.error(
		`No system browser available (tried ${CHANNELS.join(", ")}): ${lastError?.message}`,
	);
	process.exit(E_NO_BROWSER);
}

/**
 * Load a page and return its rendered text, lowercased.
 *
 * `marker` is a string the page shows only once its client fetch has resolved.
 * Waiting for it beats a fixed sleep: the dev server paints in well under a
 * second and a container can take several, so any constant is either flaky or
 * slow. `networkidle` is no good either — the shell polls service health, so
 * the network never goes quiet.
 *
 * On timeout it returns whatever did render, so the check that follows fails
 * with a readable diff instead of an exception.
 */
async function textOf(page, url, marker) {
	await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45_000 });
	if (marker) {
		try {
			await page.waitForFunction(
				(needle) => document.body.innerText.toLowerCase().includes(needle),
				marker.toLowerCase(),
				{ timeout: 30_000 },
			);
		} catch {
			/* fall through and let the assertion report what is actually there */
		}
	}
	await page.waitForTimeout(400);
	// Lowercased because several headings use text-transform: uppercase, and
	// innerText returns what is rendered rather than what is in the markup.
	return (await page.innerText("body")).toLowerCase();
}

/** Case-insensitive contains, to match textOf's normalisation. */
function has(haystack, needle) {
	return haystack.includes(needle.toLowerCase());
}

/**
 * Sign in and keep the session for the rest of the run.
 *
 * Since Phase 7 every page but /login requires an operator, so the checks below
 * would otherwise all assert against the login screen. Admin, because the audit
 * checks need it. Returns the cookie header so the direct `fetch` calls in this
 * script can use the same session the browser has.
 */
async function signIn(context, page) {
	await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 45_000 });
	await page.locator("input[autocomplete='username']").fill(OPERATOR);
	await page.locator("input[autocomplete='current-password']").fill(PASSWORD);
	await page.click('button[type="submit"]');
	// A Next soft navigation fires no `load` event, so waitForURL would hang.
	await page.waitForFunction(() => !location.pathname.startsWith("/login"), undefined, {
		timeout: 45_000,
	});
	const cookies = await context.cookies();
	return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
}

const browser = await launch();
const context = await browser.newContext();
const page = await context.newPage();

try {
	const cookieHeader = await signIn(context, page);
	check("an operator can sign in", !page.url().includes("/login"), page.url());

	// Which actor holds the refused persona?
	const actors = await (
		await fetch(`${BASE}/api/attribution/actors?limit=500`, {
			headers: { cookie: cookieHeader },
		})
	).json();
	const refusedActor = actors.find((a) => a.handles.includes("paperghost"));
	const merged = actors.filter((a) => a.persona_count > 1);
	const vector = actors.find((a) => a.label === "Vect0rShop");

	console.log("\nactors list");
	const actorsText = await textOf(page, `${BASE}/actors`, "Dr3adPirat3");
	check("actor labels render", has(actorsText, "Dr3adPirat3"));
	check("confidence bands render", has(actorsText, "CONFIRMED"));
	check("a never-merged actor reads NOT MERGED, not WEAK", has(actorsText, "NOT MERGED"));

	console.log("\nactor profile — unmeasured I");
	const profile = await textOf(page, `${BASE}/actors/${merged[0].id}`, "not assessed");
	check("component marked NOT ASSESSED", has(profile, "NOT ASSESSED"));
	check(
		"the I reason renders in full",
		has(profile, "infrastructure not assessed") && has(profile, "site-level"),
	);
	check("the redistribution is explained", has(profile, "redistributed"));
	check(
		"H, S and B still render as numbers",
		/0\.\d{3}/.test(profile) && has(profile, "stylometry") && has(profile, "behaviour"),
	);

	console.log("\nactor profile — evidence in plain language");
	check("shared identifier evidence", has(profile, "same PGP fingerprint"));
	check("behavioural evidence", has(profile, "posting-hour overlap"));
	check("stylometric evidence", has(profile, "writeprint cosine"));
	check("provenance tag on identifiers", has(profile, "extracted from prose"));

	console.log("\nactor profile — stylometry refusal");
	const refused = await textOf(page, `${BASE}/actors/${refusedActor.id}`, "stylometry refused");
	check("refusal pill", has(refused, "STYLOMETRY REFUSED"));
	check("refusal explained", has(refused, "Stylometry declined to score this persona"));
	check("the floor is named", has(refused, "300-character floor"));
	check("the character count is shown", has(refused, "152"));

	// load_fixtures.py no longer seeds identifiers that appear in no bio, post or
	// key block, so the corpus holds only what the extractor can derive. The
	// "declared only" tag stays in the UI for corpora that do carry such values;
	// here there must be nothing left for it to mark.
	console.log("\nno unreachable identifier reaches the screen");
	const vectorPage = await textOf(page, `${BASE}/actors/${vector.id}`, "same PGP fingerprint");
	check(
		"3~18 is evidenced on PGP, not the mirror onion",
		has(vectorPage, "same PGP fingerprint") && !has(vectorPage, "same mirror onion"),
	);
	check("nothing is tagged declared-only any more", !has(vectorPage, "declared only"));
	check("and 3~18 is still CONFIRMED", has(vectorPage, "confirmed"));

	console.log("\ngraph");
	const graph = await textOf(page, `${BASE}/graph`, "thickness = score");
	check("graph legend renders", has(graph, "thickness = score"));
	check("refused persona surfaced", has(graph, "refused by stylometry"));
	const edges = await page.locator('svg[role="group"] line').count();
	check("edges drawn", edges > 0, `${edges} edges`);
	if (edges > 0) {
		await page.locator('svg[role="group"] line').first().click({ force: true });
		await page.waitForTimeout(400);
		const afterClick = (await page.innerText("body")).toLowerCase();
		check(
			"clicking an edge shows its evidence",
			has(afterClick, "NOT ASSESSED") || has(afterClick, "same "),
		);
	}

	// A shared-buyer edge that renders like an attribution edge is worse than
	// one that does not render at all: the whole point of the measurement was
	// that this signal must never read as corroboration.
	console.log("\nshared buyers are context, not score");
	check("trust edges are in the legend", has(graph, "shared buyers"));
	check(
		"and labelled as not a score",
		has(graph, "context, not a score") || has(graph, "no effect on any score"),
	);
	check("with the measured reason on screen", has(graph, "0.389") && has(graph, "chance"));
	const dashed = await page.locator('svg[role="group"] line[stroke-dasharray]').count();
	check("drawn dashed, not solid", dashed > 0, `${dashed} dashed edges`);
	const solid = await page.locator('svg[role="group"] line:not([stroke-dasharray])').count();
	check(
		"and distinguishable from attribution edges",
		solid > 0 && dashed !== solid,
		`${solid} solid / ${dashed} dashed`,
	);

	const actorTrust = await textOf(page, `${BASE}/actors/1`, "shared buyers");
	check("the actor profile lists them too", has(actorTrust, "shared buyers"));
	check(
		"under a caption that says they do not count",
		has(actorTrust, "not part of this actor") || has(actorTrust, "relationship context"),
	);

	// Phase 8. Each of these is a claim the page makes about provenance, and
	// every one of them fails silently: a lead without its caveat, or a
	// template labelled as AI, looks exactly like the correct version.
	console.log("\nclearnet leads");
	const leads = await textOf(page, `${BASE}/actors/1`, "clearnet leads");
	check("the leads panel renders", has(leads, "clearnet leads"));
	check(
		"leads are worded as leads, not findings",
		has(leads, "investigative leads") || has(leads, "never conclusions"),
	);
	check(
		"actor-published leads are separated from site infrastructure",
		has(leads, "published by this actor"),
	);
	const bands = await page
		.locator("[data-band='STRONG'], [data-band='MODERATE'], [data-band='WEAK']")
		.count();
	check("each lead carries a qualitative band", bands > 0, `${bands} banded leads`);

	console.log("\nbehavioural profile");
	const profilePanel = await textOf(page, `${BASE}/actors/1`, "behavioural profile");
	check("the profile panel renders", has(profilePanel, "behavioural profile"));
	// The label is the whole feature. A profile that does not say which it is
	// is indistinguishable from one that lies about it.
	check(
		"the profile says which kind it is",
		has(profilePanel, "ai-generated summary") || has(profilePanel, "rule-based profile"),
	);
	check(
		"a rule-based profile does not claim to be AI",
		!has(profilePanel, "rule-based profile") || !has(profilePanel, "ai-generated summary"),
	);
	check(
		"and the profile is marked as not evidence",
		has(profilePanel, "not evidence") || has(profilePanel, "not part of any score"),
	);
	check("source reliability is shown on the persona", has(profilePanel, "source reliability"));

	console.log("\nthe entity graph");
	await page.goto(`${BASE}/graph`, { waitUntil: "domcontentloaded", timeout: 45_000 });
	await page.waitForTimeout(600);
	const viewToggle = page.locator("button", { hasText: "Identifiers" }).first();
	check("the graph offers an identifier view", (await viewToggle.count()) > 0);
	await viewToggle.click();
	await page.waitForTimeout(1500);
	const entity = (await page.innerText("body")).toLowerCase();
	check(
		"hubs are counted on screen",
		has(entity, "shared by more than one persona"),
		"the hub count is the point of the view",
	);
	const entityNodes = await page.locator('svg[role="img"] g[role="button"]').count();
	check("entity nodes render", entityNodes > 0, `${entityNodes} nodes`);
	check(
		"the entity view says it carries no score",
		has(entity, "no score") || has(entity, "not scored"),
	);
	// Back to personas: the default view must survive the round trip, because
	// /graph is the page the demo opens on.
	await page.locator("button", { hasText: "Personas" }).first().click();
	await page.waitForTimeout(1000);
	const back = (await page.innerText("body")).toLowerCase();
	check("switching back restores the persona view", has(back, "confirmed"));

	console.log("\ntimeline");
	const timeline = await textOf(page, `${BASE}/timeline`, "busiest bucket");
	check("chart renders", (await page.locator("svg").count()) > 0);
	check("totals render", has(timeline, "posts") && /\d/.test(timeline));

	console.log("\nexport");
	const exportText = await textOf(page, `${BASE}/export`, "I_reason");
	check("reason columns offered", has(exportText, "I_reason"));
	check("reason columns explained", has(exportText, "Keep the _reason columns"));
	check("pipeline steps offered", has(exportText, "cluster"));
	// ── accessibility ────────────────────────────────────────────────────────
	// Keyboard and structure first, then axe. Axe reports what it can see in the
	// DOM; it cannot tell you whether a drawer traps focus or whether Tab
	// actually reaches the submit button, and those are the failures that make a
	// page unusable rather than merely imperfect.

	console.log("\naccessibility — keyboard");
	await page.goto(`${BASE}/actors`, { waitUntil: "domcontentloaded" });
	await page.waitForTimeout(600);

	await page.keyboard.press("Tab");
	const firstStop = await page.evaluate(() => document.activeElement?.textContent?.trim());
	check("skip link is the first tab stop", firstStop === "Skip to content", String(firstStop));

	check(
		"the active nav item is announced, not just coloured",
		(await page.locator('a[href="/actors"][aria-current="page"]').count()) === 1,
	);
	check("the nav rail is labelled", (await page.locator("nav[aria-label]").count()) > 0);

	console.log("\naccessibility — the mobile drawer at 768px");
	await page.setViewportSize({ width: 768, height: 900 });
	await page.goto(`${BASE}/actors`, { waitUntil: "domcontentloaded" });
	await page.waitForTimeout(800);

	check(
		"no horizontal scroll at 768px",
		await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
	);

	const toggle = page.locator('button[aria-label="Toggle navigation"]');
	check("the menu toggle is shown at 768px", await toggle.isVisible());
	await toggle.focus();
	await toggle.click();
	await page.waitForTimeout(500);

	const insideRail = () =>
		page.evaluate(() => {
			const rail = document.querySelector("nav.app-layout__nav");
			return Boolean(rail?.contains(document.activeElement));
		});

	check("opening the drawer moves focus into it", await insideRail());

	// Tab past the last item — focus must wrap rather than land on the page
	// underneath, which the user cannot see.
	for (let i = 0; i < 12; i++) await page.keyboard.press("Tab");
	check("focus stays inside the open drawer", await insideRail());

	await page.keyboard.press("Escape");
	await page.waitForTimeout(500);
	check(
		"Escape closes the drawer and restores focus to the toggle",
		await page.evaluate(
			() => document.activeElement?.getAttribute("aria-label") === "Toggle navigation",
		),
	);
	await page.setViewportSize({ width: 1400, height: 900 });

	console.log("\naccessibility — axe");
	const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core"), "utf8");
	for (const path of ["/actors", "/analyze", "/graph", "/timeline", "/export", "/audit"]) {
		await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
		await page.waitForTimeout(1500);
		await page.evaluate(axeSource);
		const violations = await page.evaluate(async () => {
			// Serious and critical only. The moderate rules are largely advisory and
			// a suite that fails on them gets muted rather than fixed.
			const result = await window.axe.run(document, {
				resultTypes: ["violations"],
				runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
			});
			return result.violations
				.filter((v) => v.impact === "serious" || v.impact === "critical")
				.map((v) => `${v.id} x${v.nodes.length}`);
		});
		check(
			`axe: ${path} has no serious or critical violations`,
			violations.length === 0,
			violations.join(", "),
		);
	}
} finally {
	await browser.close();
}

const failed = checks.filter((c) => !c.ok);
console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`);
if (failed.length > 0) {
	console.error(`\nFAILED:\n${failed.map((f) => `  - ${f.name}`).join("\n")}`);
	process.exit(1);
}
