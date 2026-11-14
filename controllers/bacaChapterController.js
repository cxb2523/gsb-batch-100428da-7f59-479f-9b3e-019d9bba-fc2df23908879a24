const cheerio = require("cheerio");
const {
  BASE_URL,
  fetchHtml,
  getAbsoluteUrl,
  normalizeText,
  getImageUrl,
  extractMangaSlug,
  extractChapterNumber,
  extractChapterSlug,
  logEmptyParse,
} = require("./scraperUtils");

function getMangaSlugCandidates(slug) {
  const clean = String(slug || "")
    .trim()
    .toLowerCase()
    .replace(/^komik-/i, "")
    .replace(/-indonesia$/i, "")
    .replace(/-indo$/i, "");

  return [
    slug,
    clean,
    `komik-${clean}`,
    `${clean}-indo`,
    `${clean}-indonesia`,
    `komik-${clean}-indo`,
    `komik-${clean}-indonesia`,
  ]
    .filter(Boolean)
    .filter((v, i, a) => a.indexOf(v) === i);
}

async function checkMangaExists(slug) {
  const candidates = getMangaSlugCandidates(slug);
  for (const c of candidates) {
    try {
      await fetchHtml(`${BASE_URL}/manga/${c}/`, { timeout: 4000 });
      return true;
    } catch (e) {
      if (!e.response || e.response.status !== 404) {
        return true;
      }
    }
  }
  return false;
}

function extractSlugAndChapter(url) {
  const absoluteUrl = getAbsoluteUrl(url);
  return {
    slug: extractChapterSlug(absoluteUrl),
    chapter: extractChapterNumber(absoluteUrl),
  };
}

function getChapterInfo(link, currentSlug = "") {
  if (!link) return null;

  const originalLink = getAbsoluteUrl(link);
  const slug = extractChapterSlug(originalLink) || currentSlug;
  const chapter = extractChapterNumber(originalLink);

  return slug && chapter
    ? {
        originalLink,
        apiLink: `/baca-chapter/${slug}/${chapter}`,
        slug,
        chapter,
      }
    : null;
}

function getDescription($) {
  const descriptionText = normalizeText($("#Description").first().text());
  if (descriptionText) return descriptionText;

  return normalizeText(
    $("p")
      .filter((_, el) => /Baca online|update di Komiku/i.test($(el).text()))
      .first()
      .text()
  );
}

function chapterSortValue(chapter) {
  const normalized = String(chapter || "").replace(/-/g, ".");
  const value = parseFloat(normalized);
  return Number.isNaN(value) ? 0 : value;
}

function getChapterUrlCandidates(slug, chapter) {
  const rawChapter = String(chapter || "").trim();
  const normalizedChapter = rawChapter.replace(/\./g, "-");
  const isNumeric = /^\d+$/.test(normalizedChapter);
  const unpaddedChapter = isNumeric
    ? String(parseInt(normalizedChapter, 10))
    : normalizedChapter;
  const paddedChapter = isNumeric && unpaddedChapter.length === 1
    ? unpaddedChapter.padStart(2, "0")
    : normalizedChapter;

  const chapterCandidates = [rawChapter, normalizedChapter, paddedChapter, unpaddedChapter]
    .filter(Boolean)
    .filter((value, index, allValues) => allValues.indexOf(value) === index);

  const cleanSlug = String(slug || "").trim().toLowerCase();
  const slugCandidates = [
    cleanSlug,
    cleanSlug.replace(/^komik-/i, ""),
    cleanSlug.replace(/-indo$/i, ""),
    cleanSlug.replace(/^komik-/i, "").replace(/-indo$/i, ""),
  ].filter(Boolean).filter((value, index, allValues) => allValues.indexOf(value) === index);

  const candidates = [];
  for (const s of slugCandidates) {
    for (const c of chapterCandidates) {
      candidates.push({
        chapterValue: c,
        url: `${BASE_URL}/${s}-chapter-${c}/`,
      });
    }
  }

  return candidates;
}

function extractTitle($) {
  return (
    normalizeText($("#Judul h1").first().text()) ||
    normalizeText($("h1").first().text()) ||
    normalizeText($("meta[itemprop='name']").attr("content"))
  );
}

function extractImages($) {
  const images = [];
  $("#Baca_Komik img, img.ww, img[id]").each((_, el) => {
    const img = $(el);
    const src = getImageUrl($, img);
    const id = normalizeText(img.attr("id"));

    if (
      src &&
      /(?:[\w-]+\.)?komiku\.(?:org|to)\/(?:wp-content\/)?uploads?\d*\//i.test(src) &&
      (!id || /^\d+$/.test(id))
    ) {
      images.push({
        src,
        alt: normalizeText(img.attr("alt")),
        id,
        fallbackSrc: src.replace("cdn.komiku.org", "img.komiku.org"),
      });
    }
  });

  return images.filter(
    (image, index, allImages) =>
      image.src && allImages.findIndex((item) => item.src === image.src) === index
  );
}

// Ambil nomor chapter yang tertera di halaman (canonical, og:url,
// atribut .chapterInfo, atau teks judul) untuk diverifikasi terhadap
// nomor chapter yang diminta.
function extractPageChapter($) {
  const fromCanonical = extractChapterNumber($("link[rel='canonical']").attr("href"));
  if (fromCanonical) return fromCanonical;

  const fromOgUrl = extractChapterNumber($("meta[property='og:url']").attr("content"));
  if (fromOgUrl) return fromOgUrl;

  const fromChapterInfo = normalizeText($(".chapterInfo").attr("valuechapter"));
  if (fromChapterInfo) return fromChapterInfo;

  const titleText =
    normalizeText($("#Judul h1").first().text()) ||
    normalizeText($("h1").first().text());
  const match = titleText.match(/chapter\s+([\d]+(?:[.-]\d+)*)/i);
  return match ? match[1] : "";
}

// Samakan variasi penulisan nomor chapter: "5" = "05", "5.5" = "5-5".
function normalizeChapterForCompare(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/-/g, ".")
    .split(".")
    .map((segment) => segment.replace(/^0+(?=\d)/, ""))
    .join(".");
}

function chapterVariantsEqual(a, b) {
  const normalizedA = normalizeChapterForCompare(a);
  const normalizedB = normalizeChapterForCompare(b);
  return normalizedA !== "" && normalizedA === normalizedB;
}

async function fetchChapterHtml(slug, chapter) {
  const candidates = getChapterUrlCandidates(slug, chapter);
  const attempts = [];

  for (const candidate of candidates) {
    let data;
    try {
      data = await fetchHtml(candidate.url);
    } catch (error) {
      const statusCode = (error.response && error.response.status) || null;
      attempts.push({
        url: candidate.url,
        chapter: candidate.chapterValue,
        outcome:
          statusCode === 404
            ? "not_found"
            : statusCode === 403
              ? "blocked"
              : "error",
        statusCode,
        error: statusCode
          ? `HTTP ${statusCode}`
          : error.code || error.message || "request failed",
      });

      // 404/403: lanjut ke kandidat berikutnya. Error lain (jaringan,
      // 5xx upstream) tetap dilempar agar tidak menambah beban upstream.
      if (statusCode === 404 || statusCode === 403) continue;
      error.attempts = attempts;
      throw error;
    }

    const $ = cheerio.load(data);

    // Halaman berhasil diambil, tetapi pastikan isinya memang chapter
    // yang diminta — upstream kadang mengembalikan chapter lain untuk
    // variasi penulisan nomor yang tidak dikenalnya.
    const pageChapter = extractPageChapter($);
    if (pageChapter && !chapterVariantsEqual(chapter, pageChapter)) {
      attempts.push({
        url: candidate.url,
        chapter: candidate.chapterValue,
        outcome: "chapter_mismatch",
        statusCode: 200,
        detail: `Halaman berisi chapter ${pageChapter}, bukan chapter ${chapter} yang diminta.`,
      });
      continue;
    }

    const title = extractTitle($);
    const images = extractImages($);
    const missing = [];
    if (!title) missing.push("title");
    if (!images.length) missing.push("images");

    if (missing.length) {
      logEmptyParse("GET /baca-chapter", data, {
        target: candidate.url,
        titleFound: !!title,
        imagesFound: images.length,
        selectors: "#Judul h1, #Baca_Komik img, img.ww",
      });
      attempts.push({
        url: candidate.url,
        chapter: candidate.chapterValue,
        outcome: "parse_failed",
        statusCode: 200,
        missing,
        detail: `Halaman berhasil diambil tetapi ${missing.join(" dan ")} tidak ditemukan.`,
      });
      continue;
    }

    attempts.push({
      url: candidate.url,
      chapter: candidate.chapterValue,
      outcome: "ok",
      statusCode: 200,
    });
    return { data, chapterUrl: candidate.url, chapterValue: candidate.chapterValue, attempts };
  }

  const error = new Error(
    "Semua kandidat URL chapter gagal diambil atau tidak cocok dengan chapter yang diminta."
  );
  error.attempts = attempts;
  error.isChapterCandidatesExhausted = true;
  throw error;
}

const getBacaChapter = async (req, res) => {
  try {
    const { slug, chapter } = req.params;
    const { data, chapterUrl, chapterValue, attempts } = await fetchChapterHtml(slug, chapter);
    const $ = cheerio.load(data);

    const title = extractTitle($);
    const mangaTitleElement = $(
      '#Judul a[href*="/manga/"], a[href*="/manga/"]'
    ).first();
    const mangaTitle =
      normalizeText(mangaTitleElement.find("b").first().text()) ||
      normalizeText(mangaTitleElement.text()) ||
      normalizeText(mangaTitleElement.attr("title"));
    const mangaLink = getAbsoluteUrl(mangaTitleElement.attr("href"));
    const mangaSlug = extractMangaSlug(mangaLink);

    const chapterInfo = {};
    $("#Judul table tr, table.tbl tr").each((_, el) => {
      const cells = $(el).find("td, th");
      const key = normalizeText(cells.first().text()).replace(/:$/, "");
      const value = normalizeText(cells.last().text());
      if (key && value && key !== value) chapterInfo[key] = value;
    });

    const uniqueImages = extractImages($);

    const navigationLinks = $("#Judul")
      .parent()
      .find('a[href*="chapter"]')
      .toArray()
      .map((el) => getAbsoluteUrl($(el).attr("href")))
      .filter(Boolean);

    const currentChapterNumber = chapterSortValue(chapterValue);
    const chapterCandidates = [
      ...new Set(
        navigationLinks.filter(
          (link) =>
            extractChapterSlug(link) === slug &&
            extractChapterNumber(link) !== String(chapterValue)
        )
      ),
    ];

    const prevLink =
      chapterCandidates
        .filter((link) => chapterSortValue(extractChapterNumber(link)) < currentChapterNumber)
        .sort(
          (a, b) =>
            chapterSortValue(extractChapterNumber(b)) -
            chapterSortValue(extractChapterNumber(a))
        )[0] || "";
    const nextLink =
      chapterCandidates
        .filter((link) => chapterSortValue(extractChapterNumber(link)) > currentChapterNumber)
        .sort(
          (a, b) =>
            chapterSortValue(extractChapterNumber(a)) -
            chapterSortValue(extractChapterNumber(b))
        )[0] || "";

    const chapterValueInfo =
      extractChapterNumber(chapterUrl) ||
      $(".chapterInfo").attr("valuechapter") ||
      chapterValue;
    const totalImages =
      $(".chapterInfo").attr("valuegambar") || uniqueImages.length.toString();
    const viewAnalyticsUrl = $(".chapterInfo").attr("valueview") || "";
    const additionalDescription = normalizeText($("#Komentar p").first().text());
    const publishDate =
      $("time[property='datePublished']").attr("datetime") ||
      $("meta[itemprop='datePublished']").attr("content") ||
      normalizeText($("time").first().text());

    res.json({
      title,
      mangaInfo: {
        title: mangaTitle,
        originalLink: mangaLink,
        apiLink: mangaSlug ? `/detail-komik/${mangaSlug}` : null,
        slug: mangaSlug,
      },
      description: getDescription($),
      chapterInfo,
      images: uniqueImages,
      meta: {
        chapterNumber: chapterValueInfo,
        totalImages: parseInt(totalImages, 10) || 0,
        publishDate,
        viewAnalyticsUrl,
        slug,
      },
      navigation: {
        prevChapter: getChapterInfo(prevLink, slug),
        nextChapter: getChapterInfo(nextLink, slug),
        allChapters: mangaSlug ? `/detail-komik/${mangaSlug}` : null,
      },
      additionalDescription,
      attempts,
    });
  } catch (err) {
    const attempts = err.attempts || [];

    // Semua kandidat sudah dicoba dan tidak ada yang bisa dipakai.
    if (err.isChapterCandidatesExhausted) {
      const parseFailure = attempts.find((a) => a.outcome === "parse_failed");
      if (parseFailure) {
        const missingLabels = (parseFailure.missing || []).map((item) =>
          item === "title" ? "judul" : item === "images" ? "gambar" : item
        );
        return res.status(502).json({
          error: "Gagal parsing data chapter komik dari Komiku.",
          detail:
            `Halaman ${parseFailure.url} berhasil diambil (HTTP 200) tetapi ` +
            `${missingLabels.join(" dan ")} tidak ditemukan. Struktur HTML chapter ` +
            "kemungkinan berubah atau gambar chapter kosong.",
          source: parseFailure.url,
          missing: parseFailure.missing,
          attempts,
        });
      }

      const hasBlocked = attempts.some((a) => a.outcome === "blocked");
      if (hasBlocked) {
        // Periksa apakah komik dasarnya memang ada di Komiku
        const exists = await checkMangaExists(req.params?.slug);
        if (!exists) {
          return res.status(404).json({
            error: "Komik tidak ditemukan",
            detail: "Komik dengan slug tersebut tidak ditemukan di situs Komiku.",
            attempts,
          });
        }

        return res.status(503).json({
          error: "Upstream Cloudflare Protection",
          detail:
            "Situs upstream (komiku.org) sedang mengaktifkan Cloudflare bot challenge untuk halaman chapter. Silakan sediakan CF_CLEARANCE cookie atau gunakan proxy.",
          attempts,
        });
      }

      const triedChapters = [
        ...new Set(attempts.map((a) => a.chapter).filter(Boolean)),
      ];
      let detail =
        "Chapter komik tersebut tidak ditemukan di situs Komiku. " +
        `Variasi nomor chapter yang sudah dicoba: ${triedChapters
          .map((c) => `"${c}"`)
          .join(", ")}.`;
      const mismatches = attempts.filter((a) => a.outcome === "chapter_mismatch");
      if (mismatches.length) {
        detail += ` Kandidat berikut berisi chapter lain: ${mismatches
          .map((m) => `${m.url} (${m.detail})`)
          .join("; ")}.`;
      }
      return res.status(404).json({
        error: "Chapter tidak ditemukan",
        detail,
        attempts,
      });
    }

    if (err.response && err.response.status === 404) {
      return res.status(404).json({
        error: "Chapter tidak ditemukan",
        detail: "Chapter komik tersebut tidak ditemukan di situs Komiku.",
        attempts,
      });
    }

    if (err.response && err.response.status === 403) {
      // Periksa apakah komik dasarnya memang ada di Komiku
      const exists = await checkMangaExists(req.params?.slug);
      if (!exists) {
        return res.status(404).json({
          error: "Komik tidak ditemukan",
          detail: "Komik dengan slug tersebut tidak ditemukan di situs Komiku.",
          attempts,
        });
      }

      return res.status(503).json({
        error: "Upstream Cloudflare Protection",
        detail:
          "Situs upstream (komiku.org) sedang mengaktifkan Cloudflare bot challenge untuk halaman chapter. Silakan sediakan CF_CLEARANCE cookie atau gunakan proxy.",
        attempts,
      });
    }

    console.error("Error fetching chapter:", err);
    res.status(500).json({
      error: "Gagal mengambil data chapter komik",
      detail: err.message,
      stack: process.env.NODE_ENV === "development" ? err.stack : undefined,
      attempts,
    });
  }
};

module.exports = {
  getBacaChapter,
  extractSlugAndChapter,
  getChapterUrlCandidates,
  chapterVariantsEqual,
};
