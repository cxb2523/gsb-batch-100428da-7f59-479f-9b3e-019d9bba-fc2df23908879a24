const moment = require("moment");
const fs = require("fs");
const path = require("path");
const RLog = require("rlog-js");
const rlog = new RLog({
  logFilePath: `./logs/${moment().utcOffset(8).format("YYYYMMDDHHmmss")}.log`,
  timezone: "Asia/Shanghai",
});

const DEFAULT_STATIC_DATA_URL =
  "https://raw.githubusercontent.com/vikiboss/60s-static-host/main/static/60s";
const DEFAULT_FETCH_RETRIES = 3;
const DEFAULT_FETCH_TIMEOUT_MS = 15000;
const DEFAULT_RETRY_DELAY_BASE_MS = 2000;

function readIntEnv(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") {
    return fallback;
  }
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

const STATIC_DATA_URL = process.env.STATIC_DATA_URL || DEFAULT_STATIC_DATA_URL;
const FETCH_RETRIES = readIntEnv("FETCH_RETRIES", DEFAULT_FETCH_RETRIES);
const FETCH_TIMEOUT_MS = readIntEnv("FETCH_TIMEOUT_MS", DEFAULT_FETCH_TIMEOUT_MS);
const RETRY_DELAY_BASE_MS = readIntEnv("RETRY_DELAY_BASE_MS", DEFAULT_RETRY_DELAY_BASE_MS);
// Artifact root (latest.json / rss.xml / data/). Defaults to this script's
// directory so the production layout is unchanged; tests override it for isolation.
const ARTIFACT_DIR = process.env.NEWS_DATA_DIR
  ? path.resolve(process.env.NEWS_DATA_DIR)
  : __dirname;

// Outcome statuses emitted in the structured end-of-run summary.
const OUTCOME_STATUS = {
  SUCCESS: "success",
  UPSTREAM_MISSING: "upstream_missing",
  FETCH_FAILED: "fetch_failed",
  INVALID_FORMAT: "invalid_format",
  FAILED: "failed",
};

const OUTCOME_STATUS_TEXT = {
  [OUTCOME_STATUS.SUCCESS]: "成功",
  [OUTCOME_STATUS.UPSTREAM_MISSING]: "上游缺失",
  [OUTCOME_STATUS.FETCH_FAILED]: "抓取失败",
  [OUTCOME_STATUS.INVALID_FORMAT]: "格式无效",
  [OUTCOME_STATUS.FAILED]: "运行失败",
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatDate(date) {
  return moment(date).utcOffset(8).format("YYYY-MM-DD");
}

// Returns null when the payload is usable, otherwise a Chinese reason string.
function validateNewsPayload(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return "响应不是 JSON 对象";
  }
  if (typeof data.date !== "string" || data.date.trim() === "") {
    return "date 字段不是非空字符串";
  }
  if (!Array.isArray(data.news)) {
    return "news 字段不是数组";
  }
  if (data.news.some((item) => typeof item !== "string")) {
    return "news 数组中存在非字符串条目";
  }
  return null;
}

function atomicWriteFileSync(targetPath, content) {
  const dirPath = path.dirname(targetPath);
  fs.mkdirSync(dirPath, { recursive: true });
  const tmpPath = path.join(dirPath, `.${path.basename(targetPath)}.${process.pid}.tmp`);
  fs.writeFileSync(tmpPath, content);
  fs.renameSync(tmpPath, targetPath);
}

/**
 * Fetch and validate one date's static news file.
 * Never throws: always resolves to a structured per-date result so the caller
 * can aggregate a full picture of every attempted date.
 */
async function fetchStaticNews(date) {
  const url = `${STATIC_DATA_URL}/${date}.json`;
  let lastError = null;
  let sawFormatError = false;
  let attempts = 0;

  for (let attempt = 1; attempt <= FETCH_RETRIES; attempt++) {
    attempts = attempt;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

    try {
      const response = await fetch(url, { signal: controller.signal });

      if (response.status === 404) {
        // A missing upstream file is a definitive answer; do not waste retries.
        return {
          date,
          ok: false,
          kind: OUTCOME_STATUS.UPSTREAM_MISSING,
          attempts,
          reason: "上游文件不存在 (HTTP 404)",
          errorDetail: null,
        };
      }

      if (!response.ok) {
        throw new Error(`Static news request failed with HTTP ${response.status}.`);
      }

      // raw.githubusercontent.com currently serves these JSON files as text/plain.
      const raw = await response.text();
      let data;
      try {
        data = JSON.parse(raw);
      } catch (error) {
        sawFormatError = true;
        throw new Error(`JSON 解析失败: ${error.message}`);
      }

      const invalidReason = validateNewsPayload(data);
      if (invalidReason) {
        sawFormatError = true;
        throw new Error(`Static news response has an invalid format: ${invalidReason}.`);
      }

      return { date, ok: true, kind: null, attempts, reason: null, errorDetail: null, data };
    } catch (error) {
      lastError = error;
      if (attempt < FETCH_RETRIES) {
        const delay = attempt * RETRY_DELAY_BASE_MS;
        rlog.warning(
          `Failed to fetch ${date} (attempt ${attempt}/${FETCH_RETRIES}), retrying in ${delay}ms:`,
          error.message,
        );
        await sleep(delay);
      }
    } finally {
      clearTimeout(timeout);
    }
  }

  const kind = sawFormatError
    ? OUTCOME_STATUS.INVALID_FORMAT
    : OUTCOME_STATUS.FETCH_FAILED;
  return {
    date,
    ok: false,
    kind,
    attempts,
    reason: sawFormatError
      ? `内容校验未通过，重试 ${attempts} 次后仍为无效格式`
      : `抓取失败，重试 ${attempts} 次后仍未成功`,
    errorDetail: lastError ? lastError.message : "unknown error",
  };
}

/**
 * Try today first, then fall back to yesterday. Returns an aggregate result
 * covering every attempted date, so a failure can be classified as
 * upstream-missing vs fetch/format failure instead of a single generic error.
 */
async function getNews() {
  const today = formatDate();
  const yesterday = formatDate(moment().subtract(1, "day"));
  const attemptedDates = [today, yesterday];

  rlog.log("Start to get news from static host ...");
  const todayResult = await fetchStaticNews(today);
  if (todayResult.ok) {
    return {
      ok: true,
      adoptedDate: today,
      fallback: false,
      attemptedDates,
      details: [todayResult],
      data: todayResult.data,
    };
  }

  rlog.warning(
    `Today's static news file (${today}) is not available (${todayResult.reason}), trying ${yesterday}.`,
  );
  const yesterdayResult = await fetchStaticNews(yesterday);
  if (yesterdayResult.ok) {
    return {
      ok: true,
      adoptedDate: yesterday,
      fallback: true,
      attemptedDates,
      details: [todayResult, yesterdayResult],
      data: yesterdayResult.data,
    };
  }

  return {
    ok: false,
    adoptedDate: null,
    fallback: false,
    attemptedDates,
    details: [todayResult, yesterdayResult],
  };
}

function classifyFailure(result) {
  const kinds = result.details.map((detail) => detail.kind);
  if (kinds.every((kind) => kind === OUTCOME_STATUS.UPSTREAM_MISSING)) {
    return OUTCOME_STATUS.UPSTREAM_MISSING;
  }
  if (kinds.includes(OUTCOME_STATUS.INVALID_FORMAT)) {
    return OUTCOME_STATUS.INVALID_FORMAT;
  }
  return OUTCOME_STATUS.FETCH_FAILED;
}

// 生成RSS的函数
function generateRSS(newsData, rssPath) {
  rlog.log("Start to generate RSS ...");

  const { date, content } = newsData;
  const pubDate = moment(date, "YYYY/MM/DD").utcOffset(8).format("ddd, DD MMM YYYY HH:mm:ss ZZ");
  const buildDate = moment().utcOffset(8).format("ddd, DD MMM YYYY HH:mm:ss ZZ");

  // 转义XML特殊字符
  function escapeXml(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  // 将所有新闻组合成一个HTML格式的描述
  let newsContent = '';
  content.forEach((item, index) => {
    newsContent += `${index + 1}. ${escapeXml(item)}<br/><br/>`;
  });

  const title = `${date}`;
  const guid = `everydaynews-${date.replaceAll('/', '-')}`;

  const rssItems = `
    <item>
      <title>${escapeXml(title)}</title>
      <description><![CDATA[ ${newsContent} ]]></description>
      <pubDate>${pubDate}</pubDate>
      <guid isPermaLink="false">${guid}</guid>
      <link>https://news.ravelloh.top?date=${date.replaceAll("/", "")}</link>
    </item>`;

  const rssContent = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>EverydayNews</title>
    <link>https://news.ravelloh.top</link>
    <description>https://news.ravelloh.top</description>
    <language>zh-CN</language>
    <lastBuildDate>${buildDate}</lastBuildDate>
    <pubDate>${pubDate}</pubDate>
    <ttl>1440</ttl>
    <generator>EverydayNews RSS Generator</generator>${rssItems}
  </channel>
</rss>`;

  try {
    atomicWriteFileSync(rssPath, rssContent);
    rlog.success("RSS file generated successfully.");
    return true;
  } catch (error) {
    rlog.error("Failed to generate RSS file:", error.message);
    return false;
  }
}

/**
 * Validate, persist and (when the date changed) regenerate RSS.
 * Runs only after a usable payload has been obtained; throws on unexpected
 * local errors so no partial artifact set is left behind.
 */
function persistNews(result) {
  const origin = result.data;
  const invalidReason = validateNewsPayload(origin);
  if (invalidReason) {
    throw new Error(`Refusing to write news that failed validation: ${invalidReason}.`);
  }

  let { date, news } = origin;
  date = date.replaceAll("-", "/");
  rlog.success("Get news successfully.");
  rlog.log("Start to save news ...");

  rlog.log("Date:", date);
  rlog.log("News count:", news.length);

  const newsList = [];
  news.forEach((item) => {
    rlog.info("Processing:", item.substring(0, 16) + "...");
    newsList.push(item);
  });

  const newsData = {
    date: date,
    content: newsList,
  };
  const serialized = JSON.stringify(newsData, null, 2);

  const latestPath = path.resolve(ARTIFACT_DIR, "./latest.json");
  const rssPath = path.resolve(ARTIFACT_DIR, "./rss.xml");
  const filePath = path.resolve(ARTIFACT_DIR, `./data/${date}.json`);

  // 检查新闻是否更新：仅当日期相对旧 latest.json 发生变化时才重新生成 RSS
  let shouldUpdateRSS = true;
  try {
    if (fs.existsSync(latestPath)) {
      const latestData = JSON.parse(fs.readFileSync(latestPath, "utf8"));
      if (latestData.date === date) {
        rlog.log("News date is the same as latest.json, skipping RSS generation.");
        shouldUpdateRSS = false;
      } else {
        rlog.log(`News date changed from ${latestData.date} to ${date}, will update RSS.`);
      }
    } else {
      rlog.log("latest.json not found, will create RSS.");
    }
  } catch (error) {
    rlog.warning("Failed to read latest.json:", error.message);
    rlog.log("Will proceed with RSS generation.");
  }

  // latest.json 与日期文件使用同一份序列化内容，保证两者完全一致；
  // 原子写入避免进程中断时留下残缺文件。
  atomicWriteFileSync(filePath, serialized);
  atomicWriteFileSync(latestPath, serialized);

  let rssRegenerated = false;
  let rssError = null;
  // 只在新闻日期更新时生成RSS文件
  if (shouldUpdateRSS) {
    rssRegenerated = generateRSS(newsData, rssPath);
    if (!rssRegenerated) {
      rssError = "RSS 文件写入失败，详见上方错误日志";
    }
  } else {
    rlog.log("Skipped RSS generation as news hasn't been updated.");
  }

  rlog.success("Save news successfully.");

  return {
    dateFile: path.relative(ARTIFACT_DIR, filePath),
    latestFile: path.relative(ARTIFACT_DIR, latestPath),
    rssFile: path.relative(ARTIFACT_DIR, rssPath),
    rssRegenerated,
    rssError,
  };
}

function buildBaseOutcome() {
  return {
    event: "collect_outcome",
    timestamp: moment().utcOffset(8).format("YYYY-MM-DD HH:mm:ss ZZ"),
    status: null,
    statusText: null,
    adoptedDate: null,
    attemptedDates: [],
    fallback: false,
    reason: null,
    artifacts: null,
    details: [],
  };
}

function emitOutcome(outcome) {
  // Single greppable structured line, written to both console and the log file.
  // Strip the full news payload from per-date details: it is already persisted
  // as artifacts on success, and only adds noise to the operational log line.
  const loggable = {
    ...outcome,
    details: outcome.details.map((detail) => {
      const { data, ...rest } = detail;
      return rest;
    }),
  };
  rlog.info(`COLLECT_OUTCOME ${JSON.stringify(loggable)}`);
}

async function main() {
  const outcome = buildBaseOutcome();

  try {
    const result = await getNews();
    outcome.attemptedDates = result.attemptedDates;
    outcome.details = result.details;

    if (!result.ok) {
      const status = classifyFailure(result);
      outcome.status = status;
      outcome.statusText = OUTCOME_STATUS_TEXT[status];
      outcome.reason = result.details
        .map((detail) => `${detail.date}: ${detail.reason}（${detail.errorDetail || "无底层错误"}）`)
        .join("；");
      rlog.error(
        `Collection ended with status "${outcome.statusText}". No artifact was written. ${outcome.reason}`,
      );
      process.exitCode = 1;
      emitOutcome(outcome);
      return;
    }

    outcome.adoptedDate = result.adoptedDate;
    outcome.fallback = result.fallback;
    const artifacts = persistNews(result);
    outcome.status = OUTCOME_STATUS.SUCCESS;
    outcome.statusText = OUTCOME_STATUS_TEXT[OUTCOME_STATUS.SUCCESS];
    outcome.reason = result.fallback
      ? `当天数据不可用，已回退采用 ${result.adoptedDate} 的数据`
      : "采集成功";
    outcome.artifacts = artifacts;
    rlog.success(`Collection succeeded using ${result.adoptedDate}.`);
    emitOutcome(outcome);
  } catch (error) {
    // Defensive branch for unexpected local failures (e.g. disk errors).
    outcome.status = OUTCOME_STATUS.FAILED;
    outcome.statusText = OUTCOME_STATUS_TEXT[OUTCOME_STATUS.FAILED];
    outcome.reason = `运行发生意外错误: ${error.message}`;
    rlog.error("Collection aborted by an unexpected error:", error.message);
    process.exitCode = 1;
    emitOutcome(outcome);
  }
}

if (require.main === module) {
  rlog.log("Modules loaded successfully.");
  main();
}

module.exports = {
  OUTCOME_STATUS,
  STATIC_DATA_URL,
  FETCH_RETRIES,
  FETCH_TIMEOUT_MS,
  RETRY_DELAY_BASE_MS,
  ARTIFACT_DIR,
  validateNewsPayload,
  fetchStaticNews,
  getNews,
  classifyFailure,
  generateRSS,
  atomicWriteFileSync,
  main,
};
