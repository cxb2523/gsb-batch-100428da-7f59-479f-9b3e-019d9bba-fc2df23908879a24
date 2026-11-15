import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const RID = `MP_WXS_1234567890_WR${'a'.repeat(73)}`;
const ARTICLE_URL = 'https://mp.weixin.qq.com/s?__biz=MTIzNDU2Nzg5MA%3D%3D&mid=123&idx=1&sn=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

function runCli(mode, format) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wr-cli-'));
  const fake = path.join(dir, 'fake-cdp.mjs');
  const loader = path.join(dir, 'loader.mjs');
  const cfg = path.join(dir, 'config.json');
  const state = path.join(dir, 'quota.json');
  const trace = path.join(dir, 'trace.jsonl');
  const out = path.join(dir, `result.${format}`);
  fs.writeFileSync(cfg, JSON.stringify({
    accounts: [{ name: '示例号', bookId: 'MP_WXS_1234567890' }],
    chromePort: 9333, maxRunsPerDay: 2, maxRequestsPerDay: 40, statePath: state,
  }));
  fs.writeFileSync(loader, `export async function resolve(specifier, context, nextResolve) {
    if (specifier.endsWith('/lib/cdp.mjs')) return {url:${JSON.stringify(pathToFileURL(fake).href)},shortCircuit:true};
    return nextResolve(specifier, context);
  }`);
  fs.writeFileSync(fake, `
    import fs from 'node:fs';
    import vm from 'node:vm';
    export async function connectChrome(){return {close(){}};}
    export async function listTabs(){return [{targetId:'R',title:'示例 - 公众号 - 微信读书',url:'https://weread.qq.com/web/mp/reader/test'}];}
    export async function createTab(){throw Error('unexpected createTab');}
    export async function reloadTab(){throw Error('unexpected reload');}
    export async function evaluate(session, target, expression){
      const out = await vm.runInNewContext(expression, {
        location:{pathname:'/web/mp/reader/test'}, window:{},
        document:{title:'示例 - 公众号 - 微信读书',body:{innerText:''},querySelectorAll:()=>[]},
        AbortController, setTimeout, clearTimeout,
        fetch:async (url, options)=>{
          fs.appendFileSync(${JSON.stringify(trace)}, JSON.stringify({url,redirect:options?.redirect})+'\\n');
          // record-failed：列表请求后立即把账本路径变成目录（此时账本文件还没建，mkdir 不报错），
          // 于是转链首次 record() 就失败、单篇请求根本不会发出。
          if(url.startsWith('/web/mp/articles?') && ${mode === 'record-failed'})
            fs.mkdirSync(${JSON.stringify(state)}, {recursive:true});
          if(url.startsWith('/web/mp/articles?')) return {json:async()=>({reviews:[{subReviews:[
            {review:{reviewId:${JSON.stringify(RID)},createTime:100,mpInfo:{title:'测试文章',originalId:'WR'+'a'.repeat(73)}}},
            {review:{reviewId:'MP_WXS_1234567890_short',createTime:50,mpInfo:{title:'旧短链',originalId:'AbCdEfGhIjKlMnOpQrStUv'}}}
          ]}]})};
          if(url.startsWith('/web/mp/content?')) return {
            ok:${mode === 'success' || mode === 'commit-failed'},status:${mode === 'denied' ? 429 : 200},
            text:async()=>'<html><head><meta property="og:title" content="测试文章"><meta property="og:url" content="${ARTICLE_URL.replace(/&/g, '&amp;')}&amp;pass_ticket=synthetic-ticket"></head><body>正文不输出</body></html>'
          };
          throw Error('unexpected URL: must not visit public articles');
        }
      });
      // commit-failed：内容脚本已成功返回（record() 也已落账），在 Node 层、回到转链调度之前
      // 才把账本换成目录 —— 既不影响这次单篇结果，又保证收尾的 commitRun 必然写失败。
      if(expression.includes('/web/mp/content?') && ${mode === 'commit-failed'}) {
        fs.rmSync(${JSON.stringify(state)}, {force:true});
        fs.mkdirSync(${JSON.stringify(state)}, {recursive:true});
      }
      return out;
    }
  `);
  const child = spawnSync(process.execPath, ['--loader', pathToFileURL(loader).href,
    path.join(ROOT, 'bin/weread.mjs'), '--config', cfg, '--format', format, '--out', out], {
    encoding: 'utf8', timeout: 20000, env: { ...process.env, NODE_NO_WARNINGS: '1' },
  });
  return {
    dir, child, hasOutput: () => fs.existsSync(out),
    readOutput: () => fs.readFileSync(out, 'utf8'),
    readState: () => JSON.parse(fs.readFileSync(state, 'utf8')),
    readTrace: () => fs.readFileSync(trace, 'utf8').trim().split('\n').map(JSON.parse),
  };
}

test('转链记账不可写时不发单篇请求，仍导出已抓列表并返回失败状态', () => {
  const h = runCli('record-failed', 'json');
  try {
    assert.equal(h.child.status, 1);
    assert.ok(h.hasOutput(), '记账失败不能把已取得的列表一并丢掉');
    const output = JSON.parse(h.readOutput());
    assert.equal(output.sources[0].items.length, 2);
    const missing = output.sources[0].items.find(i => i.rid === RID);
    assert.equal(output.meta.links.aborted.reason, 'record_failed');
    assert.equal(missing.linkStatus, 'unresolved');
    assert.equal(missing.linkReason, 'not_attempted', '请求前停手必须标「批次已停止、未尝试」，不能冒充请求失败');
    assert.equal(h.readTrace().length, 1, '不能在无法记录尝试时继续请求内容');
    assert.ok(h.child.stderr.includes('账本'));
    assert.ok(h.child.stderr.includes('未结算'), '要明确交代本轮次数与列表请求尚未结算');
  } finally { fs.rmSync(h.dir, { recursive: true, force: true }); }
});

test('最终结算写账本失败时仍导出补链结果，非0退出且不重试不补发', () => {
  const h = runCli('commit-failed', 'json');
  try {
    assert.equal(h.child.status, 1, h.child.stderr);
    assert.ok(h.hasOutput(), '最终结算失败也不能吞掉已抓到的列表和已补到的链接');
    const output = JSON.parse(h.readOutput());
    const resolved = output.sources[0].items.find(i => i.rid === RID);
    assert.equal(resolved.url, ARTICLE_URL, '本轮已经补到的链接必须保留在输出里');
    assert.equal(resolved.linkStatus, 'metadata_url');
    assert.equal(output.meta.links.aborted, null, '转链批次本身没有中止，中止发生在最终结算');
    assert.equal(output.meta.links.resolved, 1);
    const trace = h.readTrace();
    assert.equal(trace.length, 2, '只允许1次列表+1次单篇：结算失败后不得自动补发');
    assert.throws(() => h.readState(), { code: 'EISDIR' }, '账本路径是目录，commit 确实写失败了');
    assert.ok(h.child.stderr.includes('账本写入失败'));
    assert.ok(h.child.stderr.includes('1 次运行次数'));
    assert.ok(h.child.stderr.includes('1 个列表请求'));
    assert.ok(h.child.stderr.includes('修复账本'));
    assert.ok(h.child.stderr.includes('不会自动重试'));
  } finally { fs.rmSync(h.dir, { recursive: true, force: true }); }
});

for (const [mode, format] of [['success', 'json'], ['denied', 'md']]) {
  test(`CLI主流程离线端到端：${mode} / ${format}`, () => {
    const h = runCli(mode, format);
    try {
      assert.equal(h.child.status, 0, h.child.stderr);
      assert.equal(h.child.stdout, '', '--out仍保持stdout静默');
      const output = h.readOutput();
      const state = h.readState();
      const trace = h.readTrace();
      assert.equal(trace.length, 2, '只允许1次列表+1次单篇，不能公开页核验或重试');
      assert.equal(trace[1].url, `/web/mp/content?reviewId=${encodeURIComponent(RID)}`);
      assert.equal(trace[1].redirect, 'error');
      assert.equal(state.count, 1, '单篇不得再扣一次运行次数');
      assert.deepEqual(state.requests, { articles: 1, shelf: 0, content: 1 });
      assert.ok(!output.includes('synthetic-ticket'));
      assert.ok(!output.includes('/s/WR'));
      assert.ok(!output.includes('正文不输出'));
      if (mode === 'success') {
        const result = JSON.parse(output);
        assert.equal(result.sources[0].items[0].url, ARTICLE_URL);
        assert.equal(result.sources[0].items[0].linkStatus, 'metadata_url');
        assert.equal(result.meta.links.requestsTotal, 1);
        assert.equal(result.sources[0].items[1].url, 'https://mp.weixin.qq.com/s/AbCdEfGhIjKlMnOpQrStUv');
      } else {
        assert.ok(output.includes('原文链接未取得'));
        assert.ok(!output.includes('[原文]()'));
        assert.ok(h.child.stderr.includes('转链') && h.child.stderr.includes('停止'));
      }
    } finally { fs.rmSync(h.dir, { recursive: true, force: true }); }
  });
}
