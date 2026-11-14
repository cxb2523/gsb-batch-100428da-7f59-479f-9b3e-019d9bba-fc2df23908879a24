"""
Mihomo 核心一键管理：下载/解压/启动/停止。

设计目标
- 将 Mihomo 核心直接放进仓库的 bin/mihomo/<平台>/ 目录，避免依赖外部客户端。
- 通过 Python 启动/停止核心进程，避免修改系统代理，做到“用完即停”。
- 跨平台支持 macOS/Windows/Linux（常见 x86_64/arm64 架构）。

常用示例
- 下载指定发行版资产到当前平台目录：
    python mihomo_manager.py download --url https://.../mihomo-darwin-arm64-v1.18.9.gz
- 启动本地核心（使用 proxies/clash_profile.yaml）并强制切到 global 模式：
    python mihomo_manager.py start --set-global
- 查看状态或停止：
    python mihomo_manager.py status
    python mihomo_manager.py stop
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

import requests


BASE_DIR = Path(__file__).resolve().parent.parent
BIN_ROOT = BASE_DIR / "bin" / "mihomo"
RUNTIME_DIR = BASE_DIR / ".runtime"
DEFAULT_CONFIG = BASE_DIR / "proxies" / "clash_profile.yaml"
DEFAULT_API = "http://127.0.0.1:19090"
PID_FILE = RUNTIME_DIR / "mihomo.pid"
LOG_FILE = RUNTIME_DIR / "mihomo.log"


def detect_platform() -> str:
    """返回标准化的平台标识，例如 macos-arm64 / macos-amd64 / windows-amd64 / linux-amd64。"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system.startswith("darwin"):
        sys_name = "macos"
    elif system.startswith("windows"):
        sys_name = "windows"
    elif system.startswith("linux"):
        sys_name = "linux"
    else:
        raise RuntimeError(f"未识别的操作系统: {system}")

    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "amd64"
    else:
        arch = machine

    return f"{sys_name}-{arch}"


def binary_dir(platform_id: Optional[str] = None) -> Path:
    """返回指定平台的二进制目录。"""
    plat = platform_id or detect_platform()
    return BIN_ROOT / plat


def binary_name(platform_id: Optional[str] = None) -> str:
    """根据平台返回可执行文件名。"""
    plat = (platform_id or detect_platform()).lower()
    return "mihomo.exe" if plat.startswith("windows") else "mihomo"


def find_binary(platform_id: Optional[str] = None) -> Optional[Path]:
    """在 bin/mihomo/<platform>/ 下查找可执行文件。"""
    exe_name = binary_name(platform_id)
    candidates = list(binary_dir(platform_id).glob("*"))
    for cand in candidates:
        if cand.name == exe_name and cand.is_file():
            return cand
    # 兜底：找包含 mihomo 字样的文件并重命名
    for cand in candidates:
        if cand.is_file() and "mihomo" in cand.name.lower():
            target = binary_dir(platform_id) / exe_name
            shutil.move(str(cand), target)
            return target
    return None


def ensure_executable(path: Path) -> None:
    """在类 Unix 平台上添加执行权限。"""
    if os.name != "nt":
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)


def download_binary(url: str, platform_id: Optional[str] = None) -> Path:
    """
    下载并解压 Mihomo 资产到指定平台目录。
    不猜测版本号，直接使用用户提供的 URL，支持 .gz / .zip / 纯二进制。
    """
    target_dir = binary_dir(platform_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = url.split("/")[-1]
    temp_path = target_dir / filename

    print(f"开始下载: {url}")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(temp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    # 解压或直接使用
    if filename.endswith(".zip"):
        with zipfile.ZipFile(temp_path, "r") as zf:
            zf.extractall(target_dir)
    elif filename.endswith(".gz"):
        # 官方发布通常是单文件 gzip
        raw_name = filename[:-3] or "mihomo"
        raw_path = target_dir / raw_name
        with gzip.open(temp_path, "rb") as gz, open(raw_path, "wb") as out_f:
            shutil.copyfileobj(gz, out_f)
    else:
        # 视为已解压好的二进制
        pass

    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass

    exe_path = find_binary(platform_id)
    if not exe_path:
        raise RuntimeError(f"未能在 {target_dir} 找到 Mihomo 可执行文件，请手动检查解压内容。")

    ensure_executable(exe_path)
    print(f"已就绪: {exe_path}")
    return exe_path


def is_api_alive(api_url: str = DEFAULT_API) -> bool:
    """检测 Mihomo 控制端是否可用。"""
    try:
        resp = requests.get(f"{api_url}/configs", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def wait_for_api(api_url: str = DEFAULT_API, timeout: float = 12.0) -> bool:
    """等待 Mihomo 控制端就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_api_alive(api_url):
            return True
        time.sleep(0.5)
    return False


def set_global_mode(api_url: str = DEFAULT_API) -> None:
    """尝试将模式切换为 global。"""
    try:
        resp = requests.patch(
            f"{api_url}/configs",
            json={"mode": "global"},
            timeout=3,
        )
        if resp.status_code == 204:
            print("已切换为 global 模式")
        else:
            print(f"切换模式失败: HTTP {resp.status_code}")
    except Exception as exc:
        print(f"切换模式失败: {exc}")


def read_pid() -> Optional[int]:
    """读取 PID 文件。"""
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def write_pid(pid: int) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def kill_pid(pid: int) -> None:
    """跨平台结束进程。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return


def reload_config(api_url: str = DEFAULT_API, config_path: Path = DEFAULT_CONFIG) -> bool:
    """通过 API 重载配置文件（热更新）。"""
    try:
        resp = requests.put(
            f"{api_url}/configs",
            params={"force": "true"},
            json={"path": str(config_path.absolute())},
            timeout=5,
        )
        if resp.status_code in (200, 204):
            print("✅ 配置已重载")
            return True
        else:
            print(f"⚠️  配置重载失败: HTTP {resp.status_code}")
            return False
    except Exception as exc:
        print(f"⚠️  配置重载失败: {exc}")
        return False


def start_mihomo(config_path: Path = DEFAULT_CONFIG, api_url: str = DEFAULT_API, platform_id: Optional[str] = None, set_global: bool = False, reuse_running: bool = True, force_reload: bool = False, foreground: bool = False) -> None:
    """启动内置 Mihomo 核心。如果已运行且 force_reload=True，则热重载配置。"""
    config_path = config_path.expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f"未找到配置文件: {config_path}")

    if reuse_running and is_api_alive(api_url):
        print(f"检测到 Mihomo 已在运行: {api_url}")
        if force_reload:
            reload_config(api_url, config_path)
        if set_global:
            set_global_mode(api_url)
        if not foreground:
            return
        print("\n💡 前台运行模式：按 Ctrl+C 停止代理")
        _wait_foreground(api_url)
        return

    exe_path = find_binary(platform_id)
    if not exe_path:
        plat = platform_id or detect_platform()
        raise FileNotFoundError(f"未找到可执行文件，请先放置或下载到 {binary_dir(plat)}")

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    
    # 创建mihomo工作目录，避免使用系统默认路径
    mihomo_home = RUNTIME_DIR / "mihomo_home"
    mihomo_home.mkdir(parents=True, exist_ok=True)
    
    log_f = open(LOG_FILE, "a", encoding="utf-8")
    cmd = [str(exe_path), "-d", str(mihomo_home), "-f", str(config_path)]
    print(f"启动 Mihomo: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT)
    write_pid(proc.pid)

    if not wait_for_api(api_url):
        print(f"未在预期时间内连上 {api_url}，请检查 {LOG_FILE}")
    else:
        print(f"控制端已就绪: {api_url}")
        if set_global:
            set_global_mode(api_url)
    
    if foreground:
        print("\n💡 前台运行模式：按 Ctrl+C 停止代理")
        _wait_foreground(api_url)


def _wait_foreground(api_url: str = DEFAULT_API) -> None:
    """前台运行模式：等待 Ctrl+C，然后清理资源。"""
    def signal_handler(sig, frame):
        print("\n\n🛑 收到停止信号，正在关闭 Mihomo...")
        stop_mihomo(api_url)
        print("✅ 已停止")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        print("🔥 代理已启动，保持运行中...")
        print(f"📡 代理地址: http://127.0.0.1:17890")
        print(f"🎛️  控制面板: {api_url}")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def stop_mihomo(api_url: str = DEFAULT_API) -> None:
    """停止内置 Mihomo 核心。"""
    pid = read_pid()
    if pid:
        print(f"停止进程 PID={pid}")
        kill_pid(pid)
        time.sleep(0.5)
        remove_pid()
    else:
        print("未找到 PID 文件，尝试通过 API 检测。")

    if is_api_alive(api_url):
        print(f"{api_url} 仍可访问，可能是其他实例在运行。")
    else:
        print("已停止或未检测到运行中的 Mihomo。")


def status(api_url: str = DEFAULT_API) -> None:
    """打印当前状态。"""
    pid = read_pid()
    running = is_api_alive(api_url)
    print(f"控制端: {api_url} -> {'在线' if running else '离线'}")
    print(f"二进制目录: {binary_dir()}")
    if pid:
        print(f"PID 文件: {pid}")
    else:
        print("PID 文件: 未找到")
    if LOG_FILE.exists():
        print(f"日志: {LOG_FILE}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mihomo 核心管理器")
    sub = parser.add_subparsers(dest="command", required=True)

    dl = sub.add_parser("download", help="下载并解压 Mihomo 资产")
    dl.add_argument("--url", required=True, help="发行版资产直链（zip/gz/二进制）")
    dl.add_argument("--platform", default=None, help="目标平台目录，默认自动检测，例如 macos-arm64")

    st = sub.add_parser("start", help="启动内置 Mihomo 核心")
    st.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    st.add_argument("--api", default=DEFAULT_API, help="控制端地址，默认 http://127.0.0.1:19090")
    st.add_argument("--platform", default=None, help="使用的二进制平台目录")
    st.add_argument("--set-global", action="store_true", help="启动后强制切到 global 模式")
    st.add_argument("--no-reuse", action="store_true", help="不复用已运行的实例，直接启动新进程（通常无需使用）")
    st.add_argument("--reload", action="store_true", help="如果已运行，则通过API热重载配置")
    st.add_argument("-f", "--foreground", action="store_true", help="前台运行，按 Ctrl+C 自动停止")

    sp = sub.add_parser("stop", help="停止内置 Mihomo 核心")
    sp.add_argument("--api", default=DEFAULT_API, help="控制端地址")

    ss = sub.add_parser("status", help="查看状态")
    ss.add_argument("--api", default=DEFAULT_API, help="控制端地址")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "download":
        download_binary(args.url, args.platform)
    elif args.command == "start":
        start_mihomo(
            config_path=Path(args.config),
            api_url=args.api,
            platform_id=args.platform,
            set_global=args.set_global,
            reuse_running=not args.no_reuse,
            foreground=args.foreground,
            force_reload=args.reload,
        )
    elif args.command == "stop":
        stop_mihomo(api_url=args.api)
    elif args.command == "status":
        status(api_url=args.api)


if __name__ == "__main__":
    main()
