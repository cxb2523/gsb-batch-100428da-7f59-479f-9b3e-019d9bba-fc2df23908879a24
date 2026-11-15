# -*- coding: utf-8 -*-
"""阶段0 探针（GATE）：验证正文选择器 + charset 是否可复用。

用法（先用 launch_chrome 脚本起好 Chrome 并登录）：
  python probe.py --chapter <章节ID或reader链接>
  python probe.py --novel <小说ID或链接>   # 自动取目录第1章
  python probe.py --attach                 # 接管当前已打开的番茄标签页
"""
import argparse
import json
import os
from config import FANQIE_URL, OUTPUT_DIR
import browser as B
import catalog as C
from decoder import charset_health

# 候选正文选择器：旧的 + 常见新结构，逐个试命中
CANDIDATE_SELECTORS = [
    'div.muye-reader-content.noselect',
    'div.muye-reader-content',
    'div[class*="reader-content"]',
    'div[class*="chapter-content"]',
    'div[class*="content"] p',
    'article',
    '.muye-reader-content',
]


def _reader_url(chapter_id: str) -> str:
    return f'{FANQIE_URL}/reader/{chapter_id}'


def _resolve_target(args, context):
    """根据参数确定要探测的页面与说明。"""
    if args.attach:
        pages = B.find_fanqie_pages(context)
        if not pages:
            raise SystemExit('未发现已打开的番茄标签页，请先在浏览器中打开一章。')
        return pages[0], f'attach:{pages[0].url}'
    if args.chapter:
        cid = C.parse_novel_id(args.chapter)
        return B.get_or_open_page(context, _reader_url(cid)), f'chapter:{cid}'
    if args.novel:
        nid = C.parse_novel_id(args.novel)
        cat = C.fetch_catalog(nid)
        cid = cat['chapters'][0]['id']
        print(f'目录《{cat["title"]}》共{len(cat["chapters"])}章，探测第1章 {cid}')
        return B.get_or_open_page(context, _reader_url(cid)), f'novel:{nid}/ch:{cid}'
    raise SystemExit('请指定 --chapter / --novel / --attach 之一')


def _try_selectors(page) -> list:
    """对每个候选选择器抽取文本，返回命中结果。"""
    results = []
    for sel in CANDIDATE_SELECTORS:
        try:
            texts = page.eval_on_selector_all(
                sel, 'els => els.map(e => e.innerText)')
        except Exception as e:  # noqa: BLE001
            results.append({'selector': sel, 'error': str(e)[:60]})
            continue
        joined = '\n'.join(t for t in texts if t)
        results.append({'selector': sel, 'nodes': len(texts),
                        'chars': len(joined), 'text': joined})
    return results


def _probe(page, label: str):
    """核心探测：等待渲染 → 试选择器 → charset 健康检查 → 存样本。"""
    print(f'\n=== 探测目标: {label} ===')
    page.wait_for_timeout(1500)
    B.wait_manual_verify(page)
    try:
        page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:  # noqa: BLE001
        pass

    hits = _try_selectors(page)
    hits_with_text = [h for h in hits if h.get('chars', 0) > 50]
    print('\n候选选择器命中情况:')
    for h in hits:
        if 'error' in h:
            print(f"  ✗ {h['selector']:42} 出错 {h['error']}")
        else:
            print(f"  {'✓' if h['chars']>50 else '·'} {h['selector']:42} "
                  f"节点{h['nodes']:>3} 字数{h['chars']:>6}")

    if not hits_with_text:
        print('\n❌ 没有选择器抽到正文。正文可能是 canvas/乱序 span，'
              '或页面是验证页。已保存 HTML 供人工分析。')
        _dump_html(page, label)
        return None

    best = max(hits_with_text, key=lambda h: h['chars'])
    raw = best['text']
    health = charset_health(raw)
    print(f"\n最佳选择器: {best['selector']}  (字数 {best['chars']})")
    print('charset 健康检查:')
    print(json.dumps(health, ensure_ascii=False, indent=2))
    _dump_sample(label, best['selector'], raw, health)
    _verdict(best['selector'], health)
    return best['selector'], health


def _verdict(selector: str, health: dict):
    print('\n=== 结论 ===')
    if health['no_pua']:
        print(f'✓ 正文无 PUA 密文，DOM 直出明文。选择器 [{selector}] 可用，'
              '无需解密。')
    elif health['healthy']:
        print(f'✓ 正文是 PUA 密文，charset 有效(还原率'
              f"{health['recover_rate']:.2%})。选择器 [{selector}] + 现有 "
              'charset 可复用，管线可照旧搭建。')
    else:
        print(f'⚠ 抓到密文但 charset 还原率仅 {health["recover_rate"]:.2%}，'
              '映射表可能已过期，需重建 charset（另议）。')


def _dump_sample(label, selector, raw, health):
    safe = label.replace('/', '_').replace(':', '_')
    path = os.path.join(OUTPUT_DIR, f'probe_{safe}.json')
    with open(path, 'w', encoding='UTF-8') as f:
        json.dump({'selector': selector, 'raw_sample': raw[:500],
                   'health': health}, f, ensure_ascii=False, indent=2)
    print(f'样本已存: {path}')


def _dump_html(page, label):
    safe = label.replace('/', '_').replace(':', '_')
    path = os.path.join(OUTPUT_DIR, f'probe_{safe}.html')
    try:
        with open(path, 'w', encoding='UTF-8') as f:
            f.write(page.content())
        print(f'HTML 已存: {path}')
    except Exception as e:  # noqa: BLE001
        print(f'保存 HTML 失败: {e}')


def main():
    ap = argparse.ArgumentParser(description='番茄正文探针 (阶段0 GATE)')
    ap.add_argument('--chapter', help='章节ID或 reader 链接')
    ap.add_argument('--novel', help='小说ID或链接(取第1章探测)')
    ap.add_argument('--attach', action='store_true', help='接管当前番茄标签页')
    args = ap.parse_args()

    p, browser, context = B.connect()
    try:
        page, label = _resolve_target(args, context)
        _probe(page, label)
    finally:
        browser.close()
        p.stop()


if __name__ == '__main__':
    main()
