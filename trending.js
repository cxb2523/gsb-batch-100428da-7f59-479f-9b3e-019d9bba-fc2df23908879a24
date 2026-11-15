const cheerio = require("cheerio");

// time 参数只接受这三个值
const VALID_TIMES = ["daily", "weekly", "monthly"];

// 缓存默认有效期：5 分钟
const DEFAULT_TTL_MS = 5 * 60 * 1000;

// 稳定可读的结局标记
const OUTCOME = {
    SUCCESS: "success", // 实时抓取并解析成功
    CACHE: "cache", // 命中未过期缓存（含上游失败后的回退）
    EMPTY: "empty", // 页面取到但解析出零条目
    ERROR: "error", // 上游请求失败且无可用缓存
    INVALID_TIME: "invalid_time", // time 参数非法
};

// 从 info 字段（如 "1,234 stars today"）中提取用于排序的数字
function infoScore(info) {
    const n = parseInt(String(info == null ? "" : info).replace(/,/g, ""), 10);
    return Number.isNaN(n) ? 0 : n;
}

// 解析 GitHub Trending 页面，返回榜单条目数组。
// 单个条目解析异常时跳过该条目，保留其余条目。
function parseTrendingPage(html) {
    const $ = cheerio.load(html);
    const items = [];
    $(".Box .Box-row").each(function () {
        try {
            const title = $(this).find("h1").text().replace(/\s/g, ""); // 获取标题
            if (!title) {
                throw new Error("missing title");
            }
            items.push({
                title: title,
                links: "https://github.com/" + title, // 拼接链接
                description: $(this).find("p").text().trim(), // 获取描述
                language: $(this).find(">.f6 .repo-language-color").siblings().text(), // 获取语言
                stars: $(this).find(">.f6 a").eq(0).text().trim(), // 获取 star 数
                forks: $(this).find(">.f6 a").eq(1).text().trim(), // 获取分支数
                info: $(this).find(">.f6 .float-sm-right").text().trim(), // 获取对应时期 star 信息
                avatar: $(this).find(">.f6 img").eq(0).attr("src"), // 获取首位作者头像
            });
        } catch (err) {
            // 坏条目：跳过，不整批丢弃
        }
    });

    // 回归按新增 star 数量排名
    items.sort((a, b) => infoScore(b.info) - infoScore(a.info));
    return items;
}

// 创建榜单服务。可注入：
//   fetchPage(time, language) -> Promise<html>  上游请求（网络错误或非 2xx 时应 reject）
//   ttlMs                     缓存有效期（毫秒）
//   now()                     时钟（测试可注入假时钟）
//   cache                     外部传入的 Map（测试可直接播种/检查）
function createTrendingService(options = {}) {
    const {
        fetchPage,
        ttlMs = DEFAULT_TTL_MS,
        now = () => Date.now(),
        cache = new Map(),
    } = options;
    if (typeof fetchPage !== "function") {
        throw new TypeError("createTrendingService: fetchPage is required");
    }

    const keyOf = (time, language) => time + "|" + (language || "");

    const readCache = (key) => {
        const entry = cache.get(key);
        if (entry && entry.expiresAt > now()) {
            return entry;
        }
        return null;
    };

    // 返回 { status, body }，body 中 outcome 为稳定结局标记，data 为榜单数组
    async function getList(time, language) {
        // 非法 time：直接 400，不发起上游抓取、不读写缓存
        if (!VALID_TIMES.includes(time)) {
            return {
                status: 400,
                body: {
                    outcome: OUTCOME.INVALID_TIME,
                    message: 'Invalid time "' + String(time) + '". Expected one of: ' + VALID_TIMES.join(", ") + ".",
                },
            };
        }

        const key = keyOf(time, language);

        // 同一 time+language 组合在有效期内复用上次成功结果，不重复请求上游
        const hit = readCache(key);
        if (hit) {
            return { status: 200, body: { outcome: OUTCOME.CACHE, cached: true, data: hit.items } };
        }

        let html;
        try {
            html = await fetchPage(time, language);
        } catch (err) {
            // 上游失败：若存在未过期的上次成功结果，回退到缓存并标明来自缓存
            const fallback = readCache(key);
            if (fallback) {
                return { status: 200, body: { outcome: OUTCOME.CACHE, cached: true, data: fallback.items } };
            }
            // 无可用缓存：返回可识别的失败结局，绝不把错误对象或空数组当榜单返回
            return {
                status: 502,
                body: { outcome: OUTCOME.ERROR, message: "Failed to fetch GitHub Trending page." },
            };
        }

        const items = parseTrendingPage(html);
        if (items.length === 0) {
            // 页面取到但解析为零条目：与上游失败区分开，且不写入缓存
            return { status: 200, body: { outcome: OUTCOME.EMPTY, data: [] } };
        }

        cache.set(key, { items: items, expiresAt: now() + ttlMs });
        return { status: 200, body: { outcome: OUTCOME.SUCCESS, cached: false, data: items } };
    }

    return { getList: getList, cache: cache };
}

module.exports = {
    VALID_TIMES: VALID_TIMES,
    DEFAULT_TTL_MS: DEFAULT_TTL_MS,
    OUTCOME: OUTCOME,
    parseTrendingPage: parseTrendingPage,
    createTrendingService: createTrendingService,
};
