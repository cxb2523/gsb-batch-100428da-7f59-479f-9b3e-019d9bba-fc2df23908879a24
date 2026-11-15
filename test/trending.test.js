const test = require("node:test");
const assert = require("node:assert/strict");

const { createTrendingService, parseTrendingPage, OUTCOME } = require("../trending");
const { createApp } = require("../index");

// ---------- 可控的假上游页面 ----------

function repoRow({ h1, description, language, stars, forks, info, avatar }) {
    return `
    <div class="Box-row">
      <h1>${h1}</h1>
      <p>${description}</p>
      <div class="f6 color-fg-muted mt-2">
        <span class="d-inline-block ml-0 mr-3">
          <span class="repo-language-color" style="background-color:#f1e05a"></span>
          <span itemprop="programmingLanguage">${language}</span>
        </span>
        <a href="/fake/one/stargazers">${stars}</a>
        <a href="/fake/one/forks">${forks}</a>
        <span class="float-sm-right">${info}</span>
        <img src="${avatar}" />
      </div>
    </div>`;
}

// 正常页面：两个好条目 + 一个缺标题的坏条目
const GOOD_HTML = `<html><body><div class="Box">
  ${repoRow({
      h1: " alice / alpha ",
      description: "alpha project",
      language: "JavaScript",
      stars: "1,000",
      forks: "100",
      info: "500 stars today",
      avatar: "https://avatars.example.com/a.png",
  })}
  ${repoRow({
      h1: "bob/beta",
      description: "beta project",
      language: "Python",
      stars: "2,000",
      forks: "200",
      info: "900 stars today",
      avatar: "https://avatars.example.com/b.png",
  })}
  <div class="Box-row"><p>broken row without title</p></div>
</div></body></html>`;

// 能取到但解析为零条目的页面
const EMPTY_HTML = `<html><body><div class="Box"></div></body></html>`;

// ---------- 假请求工具 ----------

// 返回一个 fetchPage 假实现及其调用计数
function fakeFetch(behavior) {
    const state = { calls: [] };
    const fn = async (time, language) => {
        state.calls.push({ time, language });
        return behavior(time, language);
    };
    return { fn, state };
}

// ---------- 测试 ----------

test("正常解析并写入缓存：字段结构保持不变，坏条目被跳过", async () => {
    const { fn, state } = fakeFetch(() => GOOD_HTML);
    const service = createTrendingService({ fetchPage: fn });

    const result = await service.getList("daily", "JavaScript");

    assert.equal(result.status, 200);
    assert.equal(result.body.outcome, OUTCOME.SUCCESS);
    assert.equal(result.body.cached, false);

    // 坏条目被跳过，其余条目保留
    assert.equal(result.body.data.length, 2);

    // 按 info 中新增 star 数降序
    const [first, second] = result.body.data;
    assert.equal(first.title, "bob/beta");
    assert.equal(second.title, "alice/alpha");

    // 原有字段结构不变
    assert.deepEqual(Object.keys(first).sort(), [
        "avatar",
        "description",
        "forks",
        "info",
        "language",
        "links",
        "stars",
        "title",
    ]);
    assert.equal(first.links, "https://github.com/bob/beta");
    assert.equal(first.description, "beta project");
    assert.equal(first.language, "Python");
    assert.equal(first.stars, "2,000");
    assert.equal(first.forks, "200");
    assert.equal(first.info, "900 stars today");
    assert.equal(first.avatar, "https://avatars.example.com/b.png");

    // 已写入缓存
    assert.equal(service.cache.size, 1);
    assert.equal(state.calls.length, 1);
});

test("有效期内不重复请求上游，不同组合互不影响", async () => {
    const { fn, state } = fakeFetch(() => GOOD_HTML);
    const service = createTrendingService({ fetchPage: fn });

    const first = await service.getList("daily");
    assert.equal(first.body.outcome, OUTCOME.SUCCESS);

    const second = await service.getList("daily");
    assert.equal(second.body.outcome, OUTCOME.CACHE);
    assert.equal(second.body.cached, true);
    assert.deepEqual(second.body.data, first.body.data);

    // 同一组合只请求了一次上游
    assert.equal(state.calls.length, 1);

    // 不同 language 组合互不影响，会重新请求上游
    const third = await service.getList("daily", "Python");
    assert.equal(third.body.outcome, OUTCOME.SUCCESS);
    assert.equal(state.calls.length, 2);
    assert.equal(service.cache.size, 2);
});

test("缓存过期后重新请求上游", async () => {
    let nowValue = 1_000_000;
    const { fn, state } = fakeFetch(() => GOOD_HTML);
    const service = createTrendingService({ fetchPage: fn, ttlMs: 1000, now: () => nowValue });

    await service.getList("weekly");
    nowValue += 2000; // 超过 TTL
    const result = await service.getList("weekly");

    assert.equal(result.body.outcome, OUTCOME.SUCCESS);
    assert.equal(state.calls.length, 2);
});

test("上游失败（网络错误 / 非 2xx）且无可用缓存时返回失败结局", async () => {
    for (const makeError of [
        () => new Error("network down"), // 网络错误
        () => Object.assign(new Error("Request failed with status code 503"), { response: { status: 503 } }), // 非 2xx
    ]) {
        const { fn } = fakeFetch(() => {
            throw makeError();
        });
        const service = createTrendingService({ fetchPage: fn });

        const result = await service.getList("daily");

        assert.equal(result.status, 502);
        assert.equal(result.body.outcome, OUTCOME.ERROR);
        assert.equal(typeof result.body.message, "string");
        // 不得把错误对象当榜单返回，也不得用空数组掩盖失败
        assert.equal(result.body.data, undefined);
        assert.ok(!(result.body instanceof Error));
        // 失败不写入缓存
        assert.equal(service.cache.size, 0);
    }
});

test("上游失败但存在未过期缓存时返回缓存结果", async () => {
    let failing = false;
    const { fn, state } = fakeFetch(() => {
        if (failing) {
            throw new Error("upstream down");
        }
        return GOOD_HTML;
    });
    const service = createTrendingService({ fetchPage: fn });

    const ok = await service.getList("monthly");
    assert.equal(ok.body.outcome, OUTCOME.SUCCESS);

    failing = true;
    const result = await service.getList("monthly");

    assert.equal(result.status, 200);
    assert.equal(result.body.outcome, OUTCOME.CACHE);
    assert.deepEqual(result.body.data, ok.body.data);
    // 缓存未过期时甚至不会再触发上游请求
    assert.equal(state.calls.length, 1);
});

test("上游请求发出后失败、期间缓存被并发请求写入时，回退到该缓存", async () => {
    let resolveGood, rejectBad;
    let call = 0;
    const fn = () => {
        call += 1;
        if (call === 1) {
            return new Promise((resolve) => (resolveGood = resolve));
        }
        return new Promise((_, reject) => (rejectBad = reject));
    };
    const service = createTrendingService({ fetchPage: fn });

    const p1 = service.getList("daily");
    const p2 = service.getList("daily");

    resolveGood(GOOD_HTML);
    const r1 = await p1; // 第一个请求成功并写入缓存
    assert.equal(r1.body.outcome, OUTCOME.SUCCESS);

    rejectBad(new Error("upstream down"));
    const r2 = await p2; // 第二个请求上游失败，但此时已有未过期缓存

    assert.equal(r2.status, 200);
    assert.equal(r2.body.outcome, OUTCOME.CACHE);
    assert.deepEqual(r2.body.data, r1.body.data);
});

test("解析为空与上游失败是不同的结局", async () => {
    const emptyService = createTrendingService({ fetchPage: fakeFetch(() => EMPTY_HTML).fn });
    const emptyResult = await emptyService.getList("daily");
    assert.equal(emptyResult.status, 200);
    assert.equal(emptyResult.body.outcome, OUTCOME.EMPTY);
    assert.deepEqual(emptyResult.body.data, []);
    // 空结果不写入缓存，下次仍会重新请求上游
    assert.equal(emptyService.cache.size, 0);

    const failService = createTrendingService({
        fetchPage: fakeFetch(() => {
            throw new Error("boom");
        }).fn,
    });
    const failResult = await failService.getList("daily");
    assert.equal(failResult.body.outcome, OUTCOME.ERROR);

    assert.notEqual(emptyResult.body.outcome, failResult.body.outcome);
});

test("非法 time 返回 400，不发起上游请求、不读写缓存", async () => {
    const { fn, state } = fakeFetch(() => GOOD_HTML);
    const service = createTrendingService({ fetchPage: fn });

    for (const bad of ["hourly", "yearly", "", "DAILY", "dailyy"]) {
        const result = await service.getList(bad, "JavaScript");
        assert.equal(result.status, 400);
        assert.equal(result.body.outcome, OUTCOME.INVALID_TIME);
        assert.equal(typeof result.body.message, "string");
    }

    assert.equal(state.calls.length, 0); // 未触发上游请求
    assert.equal(service.cache.size, 0); // 未读写缓存
});

test("parseTrendingPage：部分条目解析异常时跳过坏条目", () => {
    const items = parseTrendingPage(GOOD_HTML);
    assert.equal(items.length, 2);
    assert.ok(items.every((item) => item.title.length > 0));
});

test("HTTP 层：非法 time 返回 400 与 JSON 结局标记，正常请求返回 200", async () => {
    const { fn, state } = fakeFetch(() => GOOD_HTML);
    const service = createTrendingService({ fetchPage: fn });
    const app = createApp(service);
    const server = await new Promise((resolve) => {
        const s = app.listen(0, () => resolve(s));
    });
    const base = "http://127.0.0.1:" + server.address().port;
    try {
        const bad = await fetch(base + "/list/hourly");
        assert.equal(bad.status, 400);
        const badBody = await bad.json();
        assert.equal(badBody.outcome, OUTCOME.INVALID_TIME);
        assert.equal(state.calls.length, 0);

        const ok = await fetch(base + "/list/daily/JavaScript");
        assert.equal(ok.status, 200);
        const okBody = await ok.json();
        assert.equal(okBody.outcome, OUTCOME.SUCCESS);
        assert.equal(okBody.data.length, 2);

        const cached = await fetch(base + "/list/daily/JavaScript");
        const cachedBody = await cached.json();
        assert.equal(cachedBody.outcome, OUTCOME.CACHE);
        assert.equal(state.calls.length, 1);
    } finally {
        server.close();
    }
});
