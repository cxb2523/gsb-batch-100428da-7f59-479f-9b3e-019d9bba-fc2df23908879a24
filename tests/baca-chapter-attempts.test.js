/**
 * Tes lokal untuk rantai pengambilan /baca-chapter/:slug/:chapter.
 *
 * Memakai server HTTP palsu sebagai upstream (KOMIKU_BASE_URL) sehingga
 * tidak menyentuh komiku.org sama sekali. Skenario yang dicakup:
 *   1. Semua variasi nomor chapter 404          -> respons 404 + attempts
 *   2. Kandidat pertama isinya chapter lain     -> dilewati, kandidat
 *      berikutnya yang cocok dipakai (200 + attempts mismatch -> ok)
 *   3. Halaman berhasil diambil tapi gambar kosong -> respons 502 + attempts
 *
 * Jalankan: node tests/baca-chapter-attempts.test.js
 */
const http = require("http");

const UPSTREAM_PORT = 4321;
const APP_PORT = 3210;
const UPSTREAM_BASE = `http://127.0.0.1:${UPSTREAM_PORT}`;

process.env.KOMIKU_BASE_URL = UPSTREAM_BASE;
process.env.PORT = String(APP_PORT);

// ---------------------------------------------------------------------------
// HTML palsu
// ---------------------------------------------------------------------------
function chapterPage({ slug, chapterLabel, canonicalChapter, withImages = true }) {
  const canonical = `${UPSTREAM_BASE}/${slug}-chapter-${canonicalChapter}/`;
  const images = withImages
    ? `<div id="Baca_Komik">
         <img src="https://cdn.komiku.org/uploads/12345/1.jpg" id="1" alt="halaman 1"/>
         <img src="https://cdn.komiku.org/uploads/12345/2.jpg" id="2" alt="halaman 2"/>
       </div>`
    : `<div id="Baca_Komik"></div>`;

  return `<!DOCTYPE html>
<html>
<head>
  <link rel="canonical" href="${canonical}"/>
  <meta itemprop="name" content="Baca Komik ${slug} Chapter ${chapterLabel}"/>
</head>
<body>
  <div id="Judul">
    <h1>Baca Komik ${slug} Chapter ${chapterLabel} Bahasa Indonesia</h1>
    <a href="/manga/${slug}/"><b>${slug}</b></a>
  </div>
  ${images}
</body>
</html>`;
}

// ---------------------------------------------------------------------------
// Upstream palsu
// ---------------------------------------------------------------------------
const routes = {
  // Skenario 2: kandidat "-chapter-5" berisi halaman chapter 06 (mismatch),
  // kandidat "-chapter-05" berisi chapter yang benar.
  "/manga-mismatch-chapter-5/": {
    status: 200,
    body: chapterPage({ slug: "manga-mismatch", chapterLabel: "06", canonicalChapter: "06" }),
  },
  "/manga-mismatch-chapter-05/": {
    status: 200,
    body: chapterPage({ slug: "manga-mismatch", chapterLabel: "05", canonicalChapter: "05" }),
  },
  // Skenario 3: halaman 200 dengan judul benar tapi tanpa gambar.
  "/manga-kosong-chapter-7/": {
    status: 200,
    body: chapterPage({ slug: "manga-kosong", chapterLabel: "07", canonicalChapter: "07", withImages: false }),
  },
  "/manga-kosong-chapter-07/": {
    status: 200,
    body: chapterPage({ slug: "manga-kosong", chapterLabel: "07", canonicalChapter: "07", withImages: false }),
  },
  // Skenario 1 (manga-hilang): tidak ada di map -> 404 untuk semua kandidat.
};

const upstream = http.createServer((req, res) => {
  const route = routes[req.url];
  if (!route) {
    res.writeHead(404, { "Content-Type": "text/html" });
    res.end("<html><body>404 Not Found</body></html>");
    return;
  }
  res.writeHead(route.status, { "Content-Type": "text/html" });
  res.end(route.body);
});

// ---------------------------------------------------------------------------
// Mini test framework
// ---------------------------------------------------------------------------
let passed = 0;
let failed = 0;

function check(name, condition, extra) {
  if (condition) {
    passed++;
    console.log(`  ✅ ${name}`);
  } else {
    failed++;
    console.log(`  ❌ ${name}${extra ? ` -- ${extra}` : ""}`);
  }
}

async function getJson(path) {
  const res = await fetch(`http://127.0.0.1:${APP_PORT}${path}`, {
    headers: { "User-Agent": "local-test/1.0" },
  });
  const body = await res.json();
  return { status: res.status, body };
}

// ---------------------------------------------------------------------------
// Skenario
// ---------------------------------------------------------------------------
async function testAllVariants404() {
  console.log("\n[1] Semua variasi nomor chapter 404");
  const { status, body } = await getJson("/baca-chapter/manga-hilang/5");

  check("status 404", status === 404, `dapat ${status}`);
  check("ada pesan error", body.error === "Chapter tidak ditemukan");
  check(
    "detail menyebutkan variasi yang dicoba",
    typeof body.detail === "string" &&
      body.detail.includes('"5"') &&
      body.detail.includes('"05"'),
    body.detail
  );
  check("attempts adalah array berisi 2 kandidat", Array.isArray(body.attempts) && body.attempts.length === 2,
    JSON.stringify(body.attempts));
  check(
    "semua attempts berujung not_found dengan statusCode 404",
    (body.attempts || []).every((a) => a.outcome === "not_found" && a.statusCode === 404)
  );
  check(
    "attempts mencatat URL kandidat berurutan",
    body.attempts?.[0]?.url === `${UPSTREAM_BASE}/manga-hilang-chapter-5/` &&
      body.attempts?.[1]?.url === `${UPSTREAM_BASE}/manga-hilang-chapter-05/`,
    JSON.stringify(body.attempts)
  );
}

async function testMismatchThenMatch() {
  console.log("\n[2] Kandidat pertama isinya chapter lain, kandidat kedua cocok");
  const { status, body } = await getJson("/baca-chapter/manga-mismatch/5");

  check("status 200", status === 200, `dapat ${status}: ${JSON.stringify(body).slice(0, 200)}`);
  check(
    "judul berasal dari kandidat kedua (Chapter 05)",
    typeof body.title === "string" && body.title.includes("Chapter 05"),
    body.title
  );
  check("images tidak kosong", Array.isArray(body.images) && body.images.length === 2);
  check("images[0].src ada", !!body.images?.[0]?.src);
  check("meta.chapterNumber = 05", body.meta?.chapterNumber === "05", JSON.stringify(body.meta));
  check("navigation ada", body.navigation && "allChapters" in body.navigation);
  check(
    "attempts[0] = chapter_mismatch (HTTP 200)",
    body.attempts?.[0]?.outcome === "chapter_mismatch" && body.attempts?.[0]?.statusCode === 200,
    JSON.stringify(body.attempts?.[0])
  );
  check(
    "attempts[0].detail menyebut chapter 06",
    typeof body.attempts?.[0]?.detail === "string" && body.attempts[0].detail.includes("06"),
    body.attempts?.[0]?.detail
  );
  check(
    "attempts[1] = ok",
    body.attempts?.[1]?.outcome === "ok" && body.attempts?.[1]?.statusCode === 200,
    JSON.stringify(body.attempts?.[1])
  );
}

async function testEmptyImages() {
  console.log("\n[3] Halaman 200 tapi area gambar kosong");
  const { status, body } = await getJson("/baca-chapter/manga-kosong/7");

  check("status 502", status === 502, `dapat ${status}: ${JSON.stringify(body).slice(0, 200)}`);
  check("ada pesan error", typeof body.error === "string" && body.error.length > 0);
  check(
    "missing berisi images",
    Array.isArray(body.missing) && body.missing.includes("images"),
    JSON.stringify(body.missing)
  );
  check(
    "source menunjuk kandidat yang gagal diparse",
    body.source === `${UPSTREAM_BASE}/manga-kosong-chapter-7/`,
    body.source
  );
  check(
    "detail menyebut URL kandidat dan item yang hilang",
    typeof body.detail === "string" &&
      body.detail.includes("manga-kosong-chapter-7") &&
      body.detail.includes("gambar"),
    body.detail
  );
  check(
    "semua attempts parse_failed dengan missing images",
    Array.isArray(body.attempts) &&
      body.attempts.length === 2 &&
      body.attempts.every(
        (a) => a.outcome === "parse_failed" && a.statusCode === 200 && a.missing?.includes("images")
      ),
    JSON.stringify(body.attempts)
  );
}

function testChapterVariantComparison() {
  console.log("\n[0] Unit: perbandingan variasi nomor chapter");
  const { chapterVariantsEqual } = require("../controllers/bacaChapterController");
  check('"5" sama dengan "05"', chapterVariantsEqual("5", "05"));
  check('"5.5" sama dengan "5-5"', chapterVariantsEqual("5.5", "5-5"));
  check('"5" tidak sama dengan "6"', !chapterVariantsEqual("5", "6"));
  check('"5" tidak sama dengan "5.5"', !chapterVariantsEqual("5", "5.5"));
  check("string kosong tidak pernah sama", !chapterVariantsEqual("", "5"));
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  await new Promise((resolve) => upstream.listen(UPSTREAM_PORT, "127.0.0.1", resolve));
  console.log(`Upstream palsu jalan di ${UPSTREAM_BASE}`);

  // require setelah env diset agar BASE_URL menunjuk ke upstream palsu
  const app = require("../index.js");
  await new Promise((r) => setTimeout(r, 700));

  try {
    testChapterVariantComparison();
    await testAllVariants404();
    await testMismatchThenMatch();
    await testEmptyImages();
  } catch (err) {
    failed++;
    console.error("Tes gagal dengan exception:", err);
  }

  console.log(`\n========================================`);
  console.log(`Passed: ${passed}, Failed: ${failed}`);
  console.log(`========================================`);

  // Tutup server dengan rapi agar proses keluar bersih
  await new Promise((resolve) => upstream.close(resolve));
  if (app.server) {
    await new Promise((resolve) => app.server.close(resolve));
  }
  process.exitCode = failed > 0 ? 1 : 0;
}

main();
