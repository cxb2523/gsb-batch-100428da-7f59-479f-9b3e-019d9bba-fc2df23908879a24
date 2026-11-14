import { ScrapeGraphAI } from "scrapegraph-js";

const sgai = ScrapeGraphAI(); // reads SGAI_API_KEY from env

const urls = [
	"https://example.com",
	"https://example.org",
	"https://example.net",
];

// Scrapes every URL with at most 3 in-flight requests.
// Failures are reported per item — the call itself never throws.
const results = await sgai.batch.scrape({
	urls,
	concurrency: 3,
	formats: [{ type: "markdown" }],
	maxRetries: 2,
});

for (const result of results) {
	if (result.status === "success") {
		console.log(`${result.url} — ${result.attempts} attempt(s), ${result.elapsedMs}ms`);
		console.log(result.data?.results.markdown?.data);
	} else {
		console.error(`${result.url} — failed after ${result.attempts} attempt(s): ${result.error}`);
	}
}
