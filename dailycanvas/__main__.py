"""命令行入口：python -m dailycanvas --config config/sources.json --output output/"""

from __future__ import annotations

import argparse
import sys

from .pipeline import PublishError, run_pipeline

for _stream in (sys.stdout, sys.stderr):  # Windows 控制台默认 GBK，避免中文输出乱码
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dailycanvas", description="每日画布出刊流水线")
    parser.add_argument("--config", default="config/sources.json", help="源清单路径")
    parser.add_argument("--output", default="output", help="输出目录")
    parser.add_argument("--edition", default=None, help="期号（YYYY-MM-DD），缺省为当天")
    args = parser.parse_args(argv)

    try:
        result = run_pipeline(args.config, args.output, args.edition)
    except PublishError as exc:
        print(f"出刊失败: {exc}", file=sys.stderr)
        return 1

    for o in result["outcomes"]:
        if o.status == "ok":
            print(f"[ok]     {o.name}: {o.item_count} 条"
                  f"（丢弃脏数据 {o.dropped} 条，{o.elapsed_ms:.0f}ms，尝试 {o.attempts} 次）")
        else:
            print(f"[failed] {o.name}: {o.error}（尝试 {o.attempts} 次）", file=sys.stderr)
    print(f"期号 {result['edition']} 出刊完成：{result['item_count']} 条 -> "
          f"{result['paths']['html']} / {result['paths']['json']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
