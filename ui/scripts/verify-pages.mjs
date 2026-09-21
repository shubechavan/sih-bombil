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
 */

import { chromium } from "playwright-core";

const BASE = process.argv[2] ?? "http://localhost:3000";
const CHANNELS = ["msedge", "chrome"];

const checks = [];
function check(name, ok, detail = "") {
	checks.push({ name, ok, detail });
	const mark = ok ? "  PASS" : "  FAIL";
	console.log(`${mark}  ${name}${detail && !ok ? ` — ${detail}` : ""}`);
}

async function launch() {
	let lastError;
	for (const channel of CHANNELS) {
		try {
			return await chromium.launch({ channel, headless: true });
		} catch (err) {
			lastError = err;
		}
	}
	throw new Error(
		`No system browser available (tried ${CHANNELS.join(", ")}): ${lastError?.message}`,
	);
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

const browser = await launch();
const page = await browser.newPage();

try {
	// Which actor holds the refused persona?
	const actors = await (await fetch(`${BASE}/api/attribution/actors?limit=500`)).json();
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
	const edges = await page.locator('svg[role="img"] line').count();
	check("edges drawn", edges > 0, `${edges} edges`);
	if (edges > 0) {
		await page.locator('svg[role="img"] line').first().click({ force: true });
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
	const dashed = await page.locator('svg[role="img"] line[stroke-dasharray]').count();
	check("drawn dashed, not solid", dashed > 0, `${dashed} dashed edges`);
	const solid = await page.locator('svg[role="img"] line:not([stroke-dasharray])').count();
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

	console.log("\ntimeline");
	const timeline = await textOf(page, `${BASE}/timeline`, "busiest bucket");
	check("chart renders", (await page.locator("svg").count()) > 0);
	check("totals render", has(timeline, "posts") && /\d/.test(timeline));

	console.log("\nexport");
	const exportText = await textOf(page, `${BASE}/export`, "I_reason");
	check("reason columns offered", has(exportText, "I_reason"));
	check("reason columns explained", has(exportText, "Keep the _reason columns"));
	check("pipeline steps offered", has(exportText, "cluster"));
} finally {
	await browser.close();
}

const failed = checks.filter((c) => !c.ok);
console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`);
if (failed.length > 0) {
	console.error(`\nFAILED:\n${failed.map((f) => `  - ${f.name}`).join("\n")}`);
	process.exit(1);
}
