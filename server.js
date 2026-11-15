const http = require('http');
const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');

// ====== 站点配置区：换网站时主要改这里 ======
// PORT：本地服务器端口。默认 3000，可以用环境变量 PORT 覆盖。
const PORT = Number(process.env.PORT || 3000);

// TARGET_HOST：目标网站源站，只写协议 + 域名，不要带最后的斜杠。
// 默认使用 example.com 作为占位示例，避免把某个真实网站当成框架默认内容。
// 真正扒站时，把这里改成目标站点，例如 https://www.example-site.com。
const TARGET_HOST = process.env.TARGET_HOST || 'https://example.com';

// MIRROR_NAME：本地镜像文件夹名。
// 规则：不管扒什么网站，所有目标网站内容都先进这个文件夹。
// 外层 index.html 永远只做框架说明页，不保存目标网站首页。
// 默认用目标域名去掉开头的 www.，例如 www.example-site.com -> example-site.com。
const MIRROR_NAME = process.env.MIRROR_NAME || 'example.com';

// START_PATH：目标站点入口路径。
// 默认从根路径 / 开始；换网站时可以改成 /cn、/zh-hans、/home 等。
const START_PATH = process.env.START_PATH || '/';

// MIRROR_AT_ROOT：让单页应用可以直接占用 / 路径。
// 部分站点会在运行时把 history 改回 /；开启后刷新仍然返回镜像首页。
// 默认关闭，不改变既有镜像的外层说明页行为。
const MIRROR_AT_ROOT = process.env.MIRROR_AT_ROOT === '1';

// REQUEST_TIMEOUT_MS：单个远程请求超时时间，防止某个资源一直卡住。
const REQUEST_TIMEOUT_MS = Number(process.env.PROXY_TIMEOUT_MS || 30000);

// MIRROR_RETRY_FAILED：显式重试开关，默认关闭。
// 设为 1 启动时读取上次补抓结局清单，把上次失败或被拒的路径加入待重试集合；
// 这些路径再次被请求时强制重新补抓，已成功的路径不会因此重抓。
const RETRY_FAILED_ENABLED = process.env.MIRROR_RETRY_FAILED === '1';

// 补抓结局清单（机器可读 JSON），固定放在镜像文件夹根目录。
// 只做记账：不会被服务器当成本地资源响应，也不会被代理回源补抓。
const MANIFEST_FILENAME = 'mirror-fetch-manifest.json';
const MANIFEST_TMP_FILENAME = `.${MANIFEST_FILENAME}.tmp`;
const MANIFEST_PATH = path.join(__dirname, MIRROR_NAME, MANIFEST_FILENAME);
const MANIFEST_TMP_PATH = path.join(__dirname, MIRROR_NAME, MANIFEST_TMP_FILENAME);

// 结局类别（机器可读代码，含义见 README）：
// saved        成功保存
// http_error   源站 HTTP 非成功
// rejected     内容校验被拒（例如 HTML 错误页伪装成图片/JSON）
// network_error 超时或网络异常
const OUTCOME = {
    SAVED: 'saved',
    HTTP_ERROR: 'http_error',
    REJECTED: 'rejected',
    NETWORK_ERROR: 'network_error'
};
const FAILED_OUTCOMES = new Set([OUTCOME.HTTP_ERROR, OUTCOME.REJECTED, OUTCOME.NETWORK_ERROR]);

// REMOTE_MIRRORS：手动远程资源映射。
// 这个数组不是绑定某一个网站的规则；如果别的网站也有同样结构，也可以继续用。
// 例子：
// { prefix: '/cdn.example.com/', origin: 'https://cdn.example.com' }
const REMOTE_MIRRORS = [];

// BUILTIN_REMOTE_MIRRORS：内置通用映射。
// 当前保持为空，避免把某个旧网站的 CDN 专用地址写死到框架里。
const BUILTIN_REMOTE_MIRRORS = [];

// IGNORED_PATH_PREFIXES：浏览器、插件、OAuth、MCP 等探测请求。
// 这些通常不是目标网站资源，不缓存，避免日志刷屏。
const IGNORED_PATH_PREFIXES = [
    '/.well-known/',
    '/bb-mcp',
    `/${MIRROR_NAME}/www.googletagmanager.com/`,
    `/${MIRROR_NAME}/www.google-analytics.com/`
];

// ====== 通用规则区：不是某个网站专用，不要随便删 ======
// 有些站点资源路径带点，例如 /etc.clientlibs/...，它不是远程域名。
// 这些前缀应当继续拼到 TARGET_HOST 后面去抓。
const SITE_PATH_PREFIXES = new Set([
    'content',
    'etc.clientlibs',
    'experiment',
    'webui',
    'auth',
    'graphql'
]);

// 判断路径第一段是否像“被本地化后的远程域名”。
// 例如 /assets.adobedtm.com/a.js 可以代理到 https://assets.adobedtm.com/a.js。
// 要求至少两个点，是为了避免把 /etc.clientlibs/... 误判成域名。
function looksLikeMirroredRemoteHost(segment) {
    return /^[a-z0-9-]+(\.[a-z0-9-]+){2,}$/i.test(segment);
}

// 运行时只重写 HTML/CSS。
// 不能重写 JS：很多压缩脚本里有正则、模板字符串和转义 URL，粗暴替换会把脚本改坏，
// 典型表现就是菜单、轮播、弹窗等交互全部点不开。
const REWRITE_TEXT_EXTS = new Set(['.html', '.css']);

// JS/JSON 只做“外链前缀 -> 本地镜像前缀”的精确替换。
// 这样离线时媒体、CMS、第三方脚本会先走 localhost，但不会破坏压缩 JS 里的正则。
const EXTERNAL_URL_REWRITE_TEXT_EXTS = new Set(['.js', '.mjs', '.json']);
const REWRITE_ASSET_EXTS = [
    'avif', 'bin', 'css', 'gif', 'html', 'ico', 'jpg', 'jpeg', 'js', 'json',
    'ktx', 'ktx2', 'mjs', 'mov', 'mp3', 'mp4', 'otf', 'png', 'svg', 'ttf',
    'wasm', 'wav', 'webm', 'webp', 'woff', 'woff2'
];

// MIME_TYPES：告诉浏览器每类文件应该怎么解析。
const MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.mjs': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.webp': 'image/webp',
    '.avif': 'image/avif',
    '.mp4': 'video/mp4',
    '.webm': 'video/webm',
    '.mov': 'video/quicktime',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.wasm': 'application/wasm',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.otf': 'font/opentype',
    '.ttf': 'font/ttf',
    '.bin': 'application/octet-stream',
    '.ktx': 'image/ktx',
    '.ktx2': 'image/ktx2',
    '.zip': 'application/zip'
};

// MAGIC_BYTES：常见二进制文件头校验。
// 作用：防止远程返回 HTML 错误页，却被保存成 jpg/png/wasm/font。
const MAGIC_BYTES = {
    '.png': [0x89, 0x50, 0x4e, 0x47],
    '.jpg': [0xff, 0xd8, 0xff],
    '.jpeg': [0xff, 0xd8, 0xff],
    '.gif': [0x47, 0x49, 0x46],
    '.webp': [0x52, 0x49, 0x46, 0x46],
    '.wasm': [0x00, 0x61, 0x73, 0x6d],
    '.woff': [0x77, 0x4f, 0x46, 0x46],
    '.woff2': [0x77, 0x4f, 0x46, 0x32],
    '.ktx': [0xab, 0x4b, 0x54, 0x58],
    '.ktx2': [0xab, 0x4b, 0x54, 0x58]
};

const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.svg']);

function isMirrorRequest(reqPath) {
    return reqPath === `/${MIRROR_NAME}` || reqPath.startsWith(`/${MIRROR_NAME}/`);
}

function stripMirrorPrefix(reqPath) {
    if (reqPath === `/${MIRROR_NAME}`) return '/';
    return reqPath.slice(MIRROR_NAME.length + 1) || '/';
}

// 页面路由通常没有扩展名，例如 /about、/cn/about。
// 本地保存时统一落成 index.html，避免浏览器把无扩展名文件当下载文件。
function isRoutePath(reqPath) {
    return path.extname(reqPath) === '';
}

function isHtmlLike(buffer) {
    const head = buffer.subarray(0, 256).toString('utf8').trimStart().toLowerCase();
    return head.startsWith('<!doctype html') || head.startsWith('<html') || head.includes('<title>');
}

function hasExpectedMagic(filePath, buffer) {
    const ext = path.extname(filePath).toLowerCase();
    const magic = MAGIC_BYTES[ext];
    if (!magic) return true;
    if (buffer.length < magic.length) return false;
    return magic.every((byte, index) => buffer[index] === byte);
}

// 这是缓存安全阀：不要把 HTML fallback 错误页存成图片、字体、JSON 等假资源。
function isValidCachedResponse(filePath, response, buffer) {
    const ext = path.extname(filePath).toLowerCase();
    const contentType = (response.headers.get('content-type') || '').toLowerCase();

    if (isHtmlLike(buffer) && ext !== '.html' && ext !== '') {
        return false;
    }

    if (ext === '.json') {
        try {
            JSON.parse(buffer.toString('utf8'));
            return true;
        } catch {
            return false;
        }
    }

    if (ext === '.js' || ext === '.mjs') {
        return !contentType.includes('text/html');
    }

    // 有些站点扩展名不准，例如 .png 实际返回 image/jpeg。
    // 只要响应明确是图片，就允许保存。
    if (IMAGE_EXTS.has(ext) && contentType.startsWith('image/')) {
        return true;
    }

    return hasExpectedMagic(filePath, buffer);
}

function ensureDirExists(filePath) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

// 把 URL 路径安全映射到项目目录内。
// 核心规则：目标网站内容必须放在 MIRROR_NAME 文件夹里。
function getLocalPath(reqPath) {
    const baseDir = __dirname;
    let safePath = decodeURIComponent(reqPath);

    if (!isMirrorRequest(safePath)) {
        safePath = path.posix.join('/', MIRROR_NAME, safePath);
    }

    const targetPath = stripMirrorPrefix(safePath);
    if (isRoutePath(targetPath)) {
        safePath = path.posix.join(safePath, 'index.html');
    }

    const normalizedPath = path.normalize(safePath).replace(/^(\.\.[/\\])+/, '');
    const localPath = path.join(baseDir, normalizedPath);
    const resolvedBase = path.resolve(baseDir);
    const resolvedLocal = path.resolve(localPath);

    if (!resolvedLocal.startsWith(resolvedBase)) {
        return null;
    }

    return localPath;
}

function getContentType(filePath, data) {
    const ext = path.extname(filePath).toLowerCase();
    if (!ext && data && isHtmlLike(data)) return MIME_TYPES['.html'];
    return MIME_TYPES[ext] || 'application/octet-stream';
}

function serveLocalFile(filePath, res) {
    fs.readFile(filePath, (err, data) => {
        if (err) {
            res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
            res.end(`Error reading file: ${err.code}`);
            return;
        }

        const ext = path.extname(filePath).toLowerCase();
        if (REWRITE_TEXT_EXTS.has(ext) || ext === '') {
            data = Buffer.from(rewriteTextForLocalMirror(data.toString('utf8')));
        } else if (EXTERNAL_URL_REWRITE_TEXT_EXTS.has(ext)) {
            data = Buffer.from(rewriteExternalUrlsForLocalMirror(data.toString('utf8')));
        }

        res.writeHead(200, {
            'Content-Type': getContentType(filePath, data),
            'Access-Control-Allow-Origin': '*'
        });
        res.end(data);
    });
}

function getMirrorEntryPath() {
    const startPath = START_PATH.startsWith('/') ? START_PATH : `/${START_PATH}`;
    return startPath === '/' ? `/${MIRROR_NAME}/` : `/${MIRROR_NAME}${startPath}`;
}

// 外层入口页由服务器注入当前配置。
// 这样 index.html 不需要写死网站名，也不需要先靠浏览器额外 fetch 才知道入口路径。
function serveStarterPage(res) {
    const filePath = path.join(__dirname, 'index.html');
    fs.readFile(filePath, 'utf8', (err, text) => {
        if (err) {
            res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
            res.end(`Error reading file: ${err.code}`);
            return;
        }

        const config = {
            targetHost: TARGET_HOST,
            mirrorName: MIRROR_NAME,
            startPath: START_PATH,
            entryPath: getMirrorEntryPath()
        };

        const html = text.replace(
            'window.__MIRROR_CONFIG__ = null;',
            `window.__MIRROR_CONFIG__ = ${JSON.stringify(config)};`
        );

        res.writeHead(200, {
            'Content-Type': MIME_TYPES['.html'],
            'Cache-Control': 'no-store',
            'Access-Control-Allow-Origin': '*'
        });
        res.end(html);
    });
}

function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function getTargetHostName() {
    return new URL(TARGET_HOST).hostname;
}

function getLocalUrlPrefixForHost(host, slash) {
    const separator = slash === '\\/' ? '\\/' : '/';

    if (host === getTargetHostName()) {
        return `${separator}${MIRROR_NAME}${separator}`;
    }

    return `${separator}${MIRROR_NAME}${separator}${host}${separator}`;
}

// 重写所有明确写出来的 http/https 外链前缀。
// 例子：
// https://cdn.example.com/a.js -> /当前镜像文件夹/cdn.example.com/a.js
// https://目标站/assets/a.js -> /当前镜像文件夹/assets/a.js
// 如果 JS 里是 https:\/\/cdn.example.com\/a.js，也保持转义斜杠形式。
function rewriteExternalUrlsForLocalMirror(text) {
    const plainUrl = /\bhttps?:\/\/([a-z0-9.-]+\.[a-z]{2,})(\/)/gi;
    const escapedUrl = /\bhttps?:\\\/\\\/([a-z0-9.-]+\.[a-z]{2,})(\\\/)/gi;

    return text
        .replace(plainUrl, (match, host, slash) => getLocalUrlPrefixForHost(host, slash))
        .replace(escapedUrl, (match, host, slash) => getLocalUrlPrefixForHost(host, slash));
}

// 把页面里的远程 URL 改成本地镜像 URL。
// 例如 https://cdn.example.com/a.js -> /example-site.com/cdn.example.com/a.js。
function rewriteTextForLocalMirror(text) {
    const extGroup = REWRITE_ASSET_EXTS.join('|');
    const mirror = escapeRegExp(MIRROR_NAME);
    const assetUrl = new RegExp('https?:\\/\\/([^/"\\\'\\s)]+)(\\/[^"\\\'\\s)]+?\\.(?:' + extGroup + ')(?:\\?[^"\\\'\\s)]*)?)', 'gi');
    const rootAsset = new RegExp('(["\\\'(=])\\/(?!\\/|' + mirror + '\\/)([^"\\\'\\s)]+?\\.(?:' + extGroup + ')(?:\\?[^"\\\'\\s)]*)?)', 'gi');
    const rootRoute = new RegExp('(["\\\'=])\\/(?!\\/|' + mirror + '\\/)([a-z]{2}(?:-[a-z]{2})?(?:\\/[^"\\\'\\s<)]*)?)', 'gi');

    return rewriteExternalUrlsForLocalMirror(text)
        .replaceAll(TARGET_HOST, `/${MIRROR_NAME}`)
        .replace(assetUrl, (match, host, assetPath) => `/${MIRROR_NAME}/${host}${assetPath}`)
        .replace(rootAsset, (match, prefix, assetPath) => `${prefix}/${MIRROR_NAME}/${assetPath}`)
        .replace(rootRoute, (match, prefix, routePath) => `${prefix}/${MIRROR_NAME}/${routePath}`);
}

async function fetchWithTimeout(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
        return await fetch(url, {
            signal: controller.signal,
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                Referer: TARGET_HOST
            }
        });
    } finally {
        clearTimeout(timer);
    }
}

function getRemoteMirror(reqPath) {
    return [...REMOTE_MIRRORS, ...BUILTIN_REMOTE_MIRRORS].find(mirror => reqPath.startsWith(mirror.prefix));
}

function getGoogleStorageTargetUrl(reqPath, search) {
    const parts = reqPath.split('/').filter(Boolean);
    if (parts.length < 2) return null;

    if (parts[0] === 'storage.googleapis.com') {
        return `https://storage.googleapis.com/${parts.slice(1).join('/')}${search}`;
    }

    if (/^[a-z0-9-]+\.appspot\.com$/i.test(parts[0])) {
        return `https://storage.googleapis.com/${parts[0]}/${parts.slice(1).join('/')}${search}`;
    }

    return null;
}

// 根据请求路径生成真正要抓取的远程 URL。
function getTargetUrl(req, reqPath) {
    const requestUrl = new URL(req.url, `http://localhost:${PORT}`);
    const targetPath = isMirrorRequest(reqPath) ? stripMirrorPrefix(reqPath) : reqPath;
    const mirror = getRemoteMirror(targetPath);

    if (mirror) {
        return `${mirror.origin}${targetPath.slice(mirror.prefix.length - 1)}${requestUrl.search}`;
    }

    const gcsUrl = getGoogleStorageTargetUrl(targetPath, requestUrl.search);
    if (gcsUrl) return gcsUrl;

    const parts = targetPath.split('/').filter(Boolean);
    if (parts.length > 1 && looksLikeMirroredRemoteHost(parts[0]) && !SITE_PATH_PREFIXES.has(parts[0])) {
        return `https://${parts[0]}/${parts.slice(1).join('/')}${requestUrl.search}`;
    }

    return `${TARGET_HOST}${targetPath}${requestUrl.search}`;
}

// ====== 补抓结局清单 ======
// 内存里始终保留最新一份清单；同一路径多次补抓只覆盖、不追加历史。
const manifestEntries = new Map();

// 待重试集合：仅在 MIRROR_RETRY_FAILED=1 时从上次清单的失败项构建。
const retryPaths = new Set();

// 清单写盘串行化：并发补抓结束时排队写入，杜绝互相覆盖或半截内容。
// manifestWriteQueue 始终代表“最后一次排队写盘”的完成状态，
// 优雅停机或测试时可 await flushFetchManifestWrites() 等待落盘。
let manifestWriteQueue = Promise.resolve();

function isManifestLocalPath(localPath) {
    if (!localPath) return false;
    const resolved = path.resolve(localPath);
    return resolved === path.resolve(MANIFEST_PATH) || resolved === path.resolve(MANIFEST_TMP_PATH);
}

// 等待所有已排队的清单写盘完成；正常请求流程不需要调用它。
async function flushFetchManifestWrites() {
    try {
        await manifestWriteQueue;
    } catch {
        // 写盘错误已在记录时告警，这里吞掉，保证 flush 本身不会抛出。
    }
}

// 启动时读取上次清单。文件不存在或无法解析都只打印提示，绝不中断启动。
function loadFetchManifest() {
    // 上次进程被强杀时可能残留临时文件；它没有任何用途，先清掉避免污染目录。
    try {
        if (fs.existsSync(MANIFEST_TMP_PATH)) fs.rmSync(MANIFEST_TMP_PATH, { force: true });
    } catch { /* 清理失败不影响启动 */ }

    if (!fs.existsSync(MANIFEST_PATH)) {
        if (RETRY_FAILED_ENABLED) {
            console.log(`[Manifest] No previous manifest at ${MANIFEST_PATH}; starting without retry queue.`);
        }
        return;
    }

    try {
        const parsed = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
        const records = parsed && Array.isArray(parsed.records) ? parsed.records : [];

        for (const record of records) {
            if (!record || typeof record.path !== 'string' || typeof record.outcome !== 'string') continue;
            manifestEntries.set(record.path, {
                path: record.path,
                outcome: record.outcome,
                reason: typeof record.reason === 'string' ? record.reason : null,
                updatedAt: typeof record.updatedAt === 'string' ? record.updatedAt : null
            });
            if (RETRY_FAILED_ENABLED && FAILED_OUTCOMES.has(record.outcome)) {
                retryPaths.add(record.path);
            }
        }

        if (RETRY_FAILED_ENABLED) {
            console.log(`[Manifest] Loaded ${manifestEntries.size} record(s); ${retryPaths.size} failed path(s) queued for retry.`);
        }
    } catch (err) {
        console.warn(`[Manifest] Unable to parse ${MANIFEST_PATH}: ${err.message}. Continuing with an empty manifest.`);
    }
}

function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// 原子写盘：先写同目录临时文件，再 rename 替换。
// 任何时刻正式清单要么是旧的完整内容，要么是新的完整内容，不会出现半截 JSON。
// Windows 上杀毒软件或文件监视可能短暂占用文件导致 rename 报 EPERM/EBUSY，
// 因此 rename 失败时短暂异步重试；最终仍失败则退化为直接覆写并清理临时文件，
// 保证清单尽量更新，同时把异常限制在记账逻辑内。
// 全程异步：绝不能在请求链路里同步忙等，否则会饿死事件循环并触发 libuv 句柄断言。
async function persistFetchManifest() {
    const records = [...manifestEntries.values()];
    const payload = JSON.stringify({
        version: 1,
        generatedAt: new Date().toISOString(),
        recordCount: records.length,
        records
    }, null, 2);

    await fs.promises.mkdir(path.dirname(MANIFEST_PATH), { recursive: true });
    await fs.promises.writeFile(MANIFEST_TMP_PATH, payload, 'utf8');

    let lastError = null;
    for (let attempt = 0; attempt < 3; attempt++) {
        try {
            await fs.promises.rename(MANIFEST_TMP_PATH, MANIFEST_PATH);
            return;
        } catch (err) {
            lastError = err;
            if (err.code !== 'EPERM' && err.code !== 'EACCES' && err.code !== 'EBUSY') break;
            await delay(50 * (attempt + 1));
        }
    }

    try {
        await fs.promises.writeFile(MANIFEST_PATH, payload, 'utf8');
        await fs.promises.rm(MANIFEST_TMP_PATH, { force: true });
    } catch (err) {
        await fs.promises.rm(MANIFEST_TMP_PATH, { force: true }).catch(() => {});
        throw err || lastError;
    }
}

// 记录一次补抓结局并排队落盘。读写失败只告警，不影响正常请求响应。
function recordFetchOutcome(reqPath, outcome, reason = null) {
    try {
        manifestEntries.set(reqPath, {
            path: reqPath,
            outcome,
            reason: reason ? String(reason) : null,
            updatedAt: new Date().toISOString()
        });

        if (outcome === OUTCOME.SAVED) {
            retryPaths.delete(reqPath);
        } else if (FAILED_OUTCOMES.has(outcome)) {
            // 重试模式下失败项继续留在集合里，方便下次启动继续重试。
            if (RETRY_FAILED_ENABLED) retryPaths.add(reqPath);
        }

        // 串行排队：上一笔落盘完成后才开始下一笔，并发补抓不会互相覆盖。
        manifestWriteQueue = manifestWriteQueue
            .then(() => persistFetchManifest())
            .catch(err => console.warn(`[Manifest] Failed to write ${MANIFEST_PATH}: ${err.message}`));
    } catch (err) {
        console.warn(`[Manifest] Failed to record outcome for ${reqPath}: ${err.message}`);
    }
}

async function proxyAndCache(req, res, localPath, reqPath) {
    const targetUrl = getTargetUrl(req, reqPath);
    console.log(`\x1b[33m[Cache Miss] ${req.url} -> ${targetUrl}\x1b[0m`);

    try {
        const response = await fetchWithTimeout(targetUrl);

        if (!response.ok) {
            console.error(`\x1b[31m[Failed] Origin status ${response.status}: ${req.url}\x1b[0m`);
            recordFetchOutcome(reqPath, OUTCOME.HTTP_ERROR, `HTTP ${response.status} ${response.statusText || ''}`.trim());
            res.writeHead(response.status, { 'Content-Type': 'text/plain; charset=utf-8' });
            res.end(`Origin responded with status: ${response.status}`);
            return;
        }

        const buffer = Buffer.from(await response.arrayBuffer());
        if (!isValidCachedResponse(localPath, response, buffer)) {
            const contentType = response.headers.get('content-type') || 'unknown';
            console.error(`\x1b[31m[Rejected] Not caching unexpected content for ${req.url} (${contentType})\x1b[0m`);
            recordFetchOutcome(reqPath, OUTCOME.REJECTED, `unexpected content-type: ${contentType}`);
            res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
            res.end(`Rejected unexpected content for ${req.url}`);
            return;
        }

        ensureDirExists(localPath);
        fs.writeFileSync(localPath, buffer);
        console.log(`\x1b[32m[Saved] ${localPath}\x1b[0m`);
        recordFetchOutcome(reqPath, OUTCOME.SAVED, `saved ${buffer.length} byte(s)`);

        // Make the first cache-miss response behave like later cache hits.
        // Otherwise newly visited pages still point at the live site until reload.
        const ext = path.extname(localPath).toLowerCase();
        let responseBuffer = buffer;
        if (REWRITE_TEXT_EXTS.has(ext) || ext === '') {
            responseBuffer = Buffer.from(rewriteTextForLocalMirror(buffer.toString('utf8')));
        } else if (EXTERNAL_URL_REWRITE_TEXT_EXTS.has(ext)) {
            responseBuffer = Buffer.from(rewriteExternalUrlsForLocalMirror(buffer.toString('utf8')));
        }

        res.writeHead(200, {
            'Content-Type': getContentType(localPath, responseBuffer),
            'Access-Control-Allow-Origin': '*'
        });
        res.end(responseBuffer);
    } catch (err) {
        const status = err.name === 'AbortError' ? 504 : 500;
        console.error(`\x1b[31m[Error] ${req.url}: ${err.message}\x1b[0m`);
        recordFetchOutcome(
            reqPath,
            OUTCOME.NETWORK_ERROR,
            err.name === 'AbortError' ? `timeout after ${REQUEST_TIMEOUT_MS}ms` : `${err.name || 'Error'}: ${err.message}`
        );
        res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end(`Proxy error: ${err.message}`);
    }
}

const server = http.createServer(async (req, res) => {
    if (req.url === '/favicon.ico') {
        res.writeHead(204);
        res.end();
        return;
    }

    let parsedUrl;
    try {
        parsedUrl = new URL(req.url, `http://localhost:${PORT}`);
    } catch {
        res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('Bad request');
        return;
    }

    // 默认在 / 打开框架说明页；MIRROR_AT_ROOT=1 时直接打开镜像。
    // 带文件夹的镜像入口始终可用，例如 /example.com/。
    const reqPath = parsedUrl.pathname === '/'
        ? (MIRROR_AT_ROOT ? `/${MIRROR_NAME}/` : '/index.html')
        : parsedUrl.pathname;

    // 给外层 index.html 用的运行时配置。
    // 这样启动页不用写死 /example.com/，会自动读取当前 server.js 顶部配置。
    if (reqPath === '/__mirror-config.json') {
        res.writeHead(200, {
            'Content-Type': 'application/json; charset=utf-8',
            'Cache-Control': 'no-store',
            'Access-Control-Allow-Origin': '*'
        });
        res.end(JSON.stringify({
            targetHost: TARGET_HOST,
            mirrorName: MIRROR_NAME,
            startPath: START_PATH,
            entryPath: getMirrorEntryPath()
        }));
        return;
    }

    if (reqPath === '/index.html') {
        serveStarterPage(res);
        return;
    }

    if (IGNORED_PATH_PREFIXES.some(prefix => reqPath === prefix || reqPath.startsWith(prefix))) {
        res.writeHead(204);
        res.end();
        return;
    }

    const localPath = getLocalPath(reqPath);
    if (!localPath) {
        res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('Forbidden');
        return;
    }

    // 结局清单只是记账文件：不响应、不回源、不参与缓存判断。
    if (isManifestLocalPath(localPath)) {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('Not found');
        return;
    }

    // 重试模式下，上次失败或被拒的路径强制重新补抓；
    // 已成功保存的路径不在集合里，仍然正常读本地，不会因此重抓。
    const shouldRetry = RETRY_FAILED_ENABLED && retryPaths.has(reqPath);
    if (!shouldRetry && fs.existsSync(localPath) && fs.statSync(localPath).isFile()) {
        serveLocalFile(localPath, res);
        return;
    }

    if (shouldRetry) {
        console.log(`\x1b[36m[Retry] Re-fetching previously failed path: ${req.url}\x1b[0m`);
    }

    await proxyAndCache(req, res, localPath, reqPath);
});

loadFetchManifest();

// 优雅停机：先让排队中的清单写盘落盘，再停止接收新连接并退出。
// 清单写盘是异步排队的，强杀进程可能丢掉最后一笔，所以先 flush。
let shuttingDown = false;
async function shutdown(reason) {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`\n${reason}; flushing fetch manifest before exit...`);
    try {
        await flushFetchManifestWrites();
    } catch { /* flush 内部已保证不抛出，这里仅作防御 */ }

    // 先停止接收新连接、断开现存连接，再让事件循环自然排空退出。
    // 不直接 process.exit：Node 在 Windows 上配合 undici keep-alive 句柄
    // 强制退出时偶发 libuv 的 UV_HANDLE_CLOSING 断言；自然停机更稳。
    server.close(() => {
        console.log('HTTP server closed.');
    });
    server.closeAllConnections && server.closeAllConnections();

    // 兜底：若仍有句柄让进程退不掉，延时强制退出（unref 不阻止自然退出）。
    const forceExitTimer = setTimeout(() => process.exit(0), 4000);
    forceExitTimer.unref();
}
process.on('SIGTERM', () => shutdown('Received SIGTERM'));
process.on('SIGINT', () => shutdown('Received SIGINT'));
// Windows 下 kill('SIGTERM') 不会执行 JS 信号处理器；通过 IPC 消息实现跨平台优雅停机。
process.on('message', (message) => {
    if (message === 'graceful-shutdown' || (message && message.type === 'graceful-shutdown')) {
        shutdown('Received graceful-shutdown message');
    }
});

server.listen(PORT, () => {
    console.log('\n==========================================================');
    console.log('\x1b[36m  Offline Mirror - Local Proxy & Crawler Server\x1b[0m');
    console.log('==========================================================');
    console.log(`Target host: \x1b[32m${TARGET_HOST}\x1b[0m`);
    console.log(`Mirror folder: \x1b[32m${MIRROR_NAME}\x1b[0m`);
    console.log(`Local starter: \x1b[32mhttp://localhost:${PORT}/\x1b[0m`);
    console.log(`Mirror entry: \x1b[32mhttp://localhost:${PORT}${getMirrorEntryPath()}\x1b[0m`);
    console.log(`Mirror at root: ${MIRROR_AT_ROOT ? '\x1b[32menabled\x1b[0m' : 'disabled'}`);
    console.log(`Request timeout: ${REQUEST_TIMEOUT_MS}ms`);
    console.log(`Retry failed fetches (MIRROR_RETRY_FAILED): ${RETRY_FAILED_ENABLED ? '\x1b[32menabled\x1b[0m' : 'disabled'}`);
    console.log(`Fetch manifest: \x1b[32m${MANIFEST_PATH}\x1b[0m`);
    console.log('Unexpected HTML fallback responses will not be cached as assets.');
    console.log('----------------------------------------------------------\n');

    const url = `http://localhost:${PORT}/`;
    const startCmd = process.platform === 'win32' ? 'start' : process.platform === 'darwin' ? 'open' : 'xdg-open';
    if (process.env.NODE_ENV === 'test') {
        console.log('Test environment detected; skipping automatic browser open.');
        return;
    }
    exec(`${startCmd} ${url}`, (err) => {
        if (err) console.error('Failed to auto-open browser:', err.message);
    });
});
