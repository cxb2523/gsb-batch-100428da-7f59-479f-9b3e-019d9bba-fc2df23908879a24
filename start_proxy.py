"""
一键管理代理：获取节点 -> 生成配置 -> 启动/停止 Mihomo 核心。

常用命令
- 获取节点：python start_proxy.py fetch
- 生成配置：python start_proxy.py generate
- 启动核心：python start_proxy.py start
- 组合启动：python start_proxy.py up --fetch --check
- 停止核心：python start_proxy.py stop
- 查看状态：python start_proxy.py status
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mihomo_manager import (  # type: ignore  # noqa: E402
    binary_dir,
    detect_platform,
    find_binary,
    is_api_alive,
)

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
PROXIES_DIR = BASE_DIR / "proxies"


def run_py(script: str, extra_args=None) -> None:
    """以仓库根目录为 cwd 调用 src 下的脚本。"""
    extra_args = extra_args or []
    cmd = [sys.executable, str(SRC_DIR / script), *extra_args]
    subprocess.run(cmd, check=True, cwd=BASE_DIR)


def cmd_fetch(_: argparse.Namespace) -> None:
    run_py("fetch_proxies.py")


def cmd_generate(_: argparse.Namespace) -> None:
    try:
        run_py("generate_clash_profile.py")
    except subprocess.CalledProcessError:
        print("❌ 生成 clash_profile.yaml 失败。请确认已填写订阅并成功拉取节点 (start_proxy.py fetch / up --fetch)。")
        raise


def cmd_check(_: argparse.Namespace) -> None:
    run_py("check_proxies.py")


def cmd_download(args: argparse.Namespace) -> None:
    """自动下载 Mihomo 内核到对应平台目录。"""
    if not args.url and not args.version:
        print("请提供 --url 或 --version（例如 v1.18.9）")
        return

    platform_id = detect_platform()
    if args.url:
        url = args.url
    else:
        asset_name = build_asset_name(platform_id, args.version)
        if not asset_name:
            print(f"未能匹配平台 {platform_id} 的资产名，请改用 --url 指定直链。")
            return
        url = f"https://github.com/MetaCubeX/mihomo/releases/download/{args.version}/{asset_name}"
        print(f"根据平台推测下载地址: {url}")
        print("如资产命名有变，请改用 --url 明确指定。")

    run_py("mihomo_manager.py", ["download", "--url", url, "--platform", platform_id])


def cmd_start(args: argparse.Namespace) -> None:
    extra = ["start"]
    if args.no_set_global:
        pass
    else:
        extra.append("--set-global")
    if args.reload:
        extra.append("--reload")
    if args.foreground:
        extra.append("--foreground")
    run_py("mihomo_manager.py", extra)


def cmd_stop(args: argparse.Namespace) -> None:
    run_py("mihomo_manager.py", ["stop"] + (["--api", args.api] if args.api else []))


def cmd_status(args: argparse.Namespace) -> None:
    run_py("mihomo_manager.py", ["status"] + (["--api", args.api] if args.api else []))


def cmd_doctor(args: argparse.Namespace) -> None:
    """环境体检：平台、内核、配置、API 状态。"""
    platform_id = detect_platform()
    print(f"平台: {platform_id}")

    bin_dir = binary_dir(platform_id)
    bin_path = find_binary(platform_id)
    print(f"内核目录: {bin_dir}")
    print(f"内核是否存在: {'是' if bin_path else '否'}")
    if not bin_path:
        print("  → 运行: python start_proxy.py download --version v1.x.x 或 --url <直链>")

    raw_nodes = PROXIES_DIR / "raw_nodes.yaml"
    clash_profile = PROXIES_DIR / "clash_profile.yaml"
    test_results = PROXIES_DIR / "proxy_test_results.json"

    print(f"raw_nodes.yaml: {'存在' if raw_nodes.exists() else '缺失'} ({raw_nodes})")
    print(f"clash_profile.yaml: {'存在' if clash_profile.exists() else '缺失'} ({clash_profile})")
    print(f"proxy_test_results.json: {'存在' if test_results.exists() else '缺失'} ({test_results})")

    if args.check_api:
        alive = is_api_alive()
        print(f"Mihomo 控制端 http://127.0.0.1:9090 状态: {'在线' if alive else '离线'}")
        if not alive:
            print("  → 如果已启动外部客户端，确认 external-controller/secret；或用 start_proxy.py start 启动内置核心。")


def cmd_up(args: argparse.Namespace) -> None:
    if args.fetch:
        cmd_fetch(args)
    cmd_generate(args)
    cmd_start(args)
    if args.check:
        cmd_check(args)


def build_asset_name(platform_id: str, version: str) -> str | None:
    """基于平台和版本推测官方资产名，覆盖常见平台。"""
    mapping = {
        "macos-arm64": "mihomo-darwin-arm64-{ver}.gz",
        "macos-amd64": "mihomo-darwin-amd64-{ver}.gz",
        "windows-amd64": "mihomo-windows-amd64-{ver}.zip",
        "linux-amd64": "mihomo-linux-amd64-{ver}.gz",
        "linux-arm64": "mihomo-linux-arm64-{ver}.gz",
    }
    template = mapping.get(platform_id)
    return template.format(ver=version) if template else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="代理快速入口")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("fetch", help="拉取机场订阅并生成 raw_nodes.yaml").set_defaults(func=cmd_fetch)
    sub.add_parser("generate", help="根据 raw_nodes.yaml 生成 clash_profile.yaml").set_defaults(func=cmd_generate)
    sub.add_parser("check", help="并发检测节点可用性，输出 proxy_test_results.json").set_defaults(func=cmd_check)

    dl_p = sub.add_parser("download", help="下载 Mihomo 内核到 bin/mihomo/<平台>/")
    dl_p.add_argument("--url", help="发行版资产直链（优先使用）")
    dl_p.add_argument("--version", help="版本号（如 v1.18.9，按平台猜资产名）")
    dl_p.set_defaults(func=cmd_download)

    start_p = sub.add_parser("start", help="启动内置 Mihomo 核心")
    start_p.add_argument("--no-set-global", action="store_true", help="启动后不强制切到 global 模式")
    start_p.add_argument("--reload", action="store_true", help="如果已运行，则热重载配置")
    start_p.add_argument("-f", "--foreground", action="store_true", help="前台运行，按 Ctrl+C 自动停止")
    start_p.set_defaults(func=cmd_start)

    stop_p = sub.add_parser("stop", help="停止内置 Mihomo 核心")
    stop_p.add_argument("--api", help="控制端地址，默认 http://127.0.0.1:9090")
    stop_p.set_defaults(func=cmd_stop)

    status_p = sub.add_parser("status", help="查看内置核心状态")
    status_p.add_argument("--api", help="控制端地址，默认 http://127.0.0.1:9090")
    status_p.set_defaults(func=cmd_status)

    doctor_p = sub.add_parser("doctor", help="环境体检：平台/内核/配置/API")
    doctor_p.add_argument("--check-api", action="store_true", help="顺便探测 http://127.0.0.1:9090 是否在线")
    doctor_p.set_defaults(func=cmd_doctor)

    up_p = sub.add_parser("up", help="一键：可选 fetch -> generate -> start -> 可选 check")
    up_p.add_argument("--fetch", action="store_true", help="启动前先拉取订阅")
    up_p.add_argument("--check", action="store_true", help="启动后跑一次节点检测")
    up_p.add_argument("--no-set-global", action="store_true", help="启动后不强制切到 global 模式")
    up_p.add_argument("--reload", action="store_true", help="如果已运行，则热重载配置")
    up_p.add_argument("-f", "--foreground", action="store_true", help="前台运行，按 Ctrl+C 自动停止")
    up_p.set_defaults(func=cmd_up)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        print("\n示例：python start_proxy.py up --fetch --check")
        return
    args.func(args)


if __name__ == "__main__":
    main()
