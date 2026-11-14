"""渲染阶段：生成 HTML 页面与 JSON 接口文件。"""

from __future__ import annotations

import html
import json
from typing import List

from .model import Entry, SourceOutcome

HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日画布 · {edition}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
h1 {{ font-size: 1.5rem; }}
.meta {{ color: #777; font-size: 0.85rem; margin-bottom: 1.5rem; }}
ol {{ padding-left: 1.4rem; }}
li {{ margin: 0.6rem 0; }}
.src {{ color: #777; font-size: 0.85rem; margin-left: 0.4rem; }}
a {{ color: #1a5fb4; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>每日画布</h1>
<p class="meta">期号：{edition} ｜ 收录 {count} 条 ｜ 来源 {ok_sources}/{total_sources} 可用</p>
<ol>
{items}
</ol>
</body>
</html>
"""

ITEM_TEMPLATE = '<li><a href="{url}">{title}</a><span class="src">[{source} · {date}]</span></li>'


def render_html(edition: str, items: List[Entry], outcomes: List[SourceOutcome]) -> str:
    rows = []
    for e in items:
        rows.append(
            ITEM_TEMPLATE.format(
                url=html.escape(e.url, quote=True) or "#",
                title=html.escape(e.title),
                source=html.escape(e.source),
                date=e.published.date().isoformat(),
            )
        )
    ok = sum(1 for o in outcomes if o.status == "ok")
    return HTML_TEMPLATE.format(
        edition=html.escape(edition),
        count=len(items),
        ok_sources=ok,
        total_sources=len(outcomes),
        items="\n".join(rows),
    )


def render_json(edition: str, items: List[Entry], outcomes: List[SourceOutcome]) -> dict:
    return {
        "meta": {
            "edition": edition,
            "item_count": len(items),
            "sources": [o.to_json() for o in outcomes],
        },
        "items": [e.to_json() for e in items],
    }


def write_outputs(edition: str, items: List[Entry], outcomes: List[SourceOutcome], output_dir) -> dict:
    """把 HTML 与 JSON 写入输出目录，返回写入路径。"""
    from pathlib import Path

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / "index.html"
    json_path = out / "daily.json"
    html_path.write_text(render_html(edition, items, outcomes), encoding="utf-8")
    json_path.write_text(
        json.dumps(render_json(edition, items, outcomes), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"html": html_path, "json": json_path}
