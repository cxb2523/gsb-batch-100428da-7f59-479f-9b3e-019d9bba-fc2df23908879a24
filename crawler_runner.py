import json
import os
import subprocess

# Scrapy 子进程最长等待时间（秒）
CRAWL_TIMEOUT = 60

# 四类失败结局的状态标识（成功时直接返回书籍列表，不使用该标识）
STATUS_TIMEOUT = "timeout"
STATUS_SUBPROCESS_FAILED = "subprocess_failed"
STATUS_NO_OUTPUT = "no_output"
STATUS_JSON_DECODE_ERROR = "json_decode_error"

# detail 中原始错误信息最多保留的字符数，避免超长 stderr 刷屏
_MAX_DETAIL_LENGTH = 4000


def _project_dir():
    """返回项目根目录（本文件所在目录），Scrapy 以此作为工作目录。"""
    return os.path.dirname(os.path.abspath(__file__))


def _truncate(text, limit=_MAX_DETAIL_LENGTH):
    """截断过长的错误输出，同时保留原始内容的主体部分。"""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（内容过长，已截断；原始长度共 {len(text)} 个字符）"


def _stream_to_text(stream):
    """TimeoutExpired 携带的 stdout/stderr 可能是 str 或 bytes，统一成文本。"""
    if not stream:
        return ""
    if isinstance(stream, bytes):
        stream = stream.decode("utf-8", errors="replace")
    return _truncate(stream)


def _error(status, reason, detail=""):
    """
    构造统一的失败返回结构。

    失败时返回 dict，与成功时的书籍列表（list）在类型上即可区分，
    模板不会把错误信息当成书籍条目渲染。
    - status: 机器可判别的失败类型标识
    - reason: 面向用户的简短中文原因，页面直接展示
    - detail: 保留下来的原始错误信息（stderr 摘要、解析报错、文件片段等）
    """
    return {
        "ok": False,
        "status": status,
        "reason": reason,
        "detail": _truncate(detail),
    }


def run_crawl(output_file=None):
    """
    运行 Scrapy 爬虫，返回值区分以下五类结局：

    1. 成功：返回书籍列表 list[dict]（原有结构，含 title/price/availability），
       index.html 按书籍条目逐条渲染；
    2. 超时（timeout）：子进程在 CRAWL_TIMEOUT 秒内未结束，返回错误字典；
    3. 子进程失败（subprocess_failed）：子进程启动失败或退出码非 0，
       detail 中保留 stderr 摘要；
    4. 无输出文件（no_output）：子进程正常结束但未生成 output.json；
    5. JSON 解析失败（json_decode_error）：output.json 存在但内容不是合法 JSON，
       detail 中保留解析报错与原文片段。

    每次运行前都会先删除旧的 output.json，因此“无输出文件”与“解析失败”
    分别可辨；超时后若残留只写了一半的 output.json，会被立即删除，
    绝不会被当作成功结果返回。
    """
    if output_file is None:
        output_file = os.path.join(_project_dir(), "output.json")

    # 每次运行前删除旧输出文件，保证后续“文件不存在”只可能是本次运行造成的
    if os.path.exists(output_file):
        os.remove(output_file)

    command = ["scrapy", "crawl", "mycrawler", "-o", output_file]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=CRAWL_TIMEOUT,
            cwd=_project_dir(),
        )
    except subprocess.TimeoutExpired as exc:
        # 超时后 Scrapy 可能已经写入了一部分 output.json（半成品），
        # 必须清理掉，并且本次运行直接按超时失败返回，不得再读取该文件。
        residual_note = ""
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
                residual_note = "已清理超时残留的半成品输出文件 output.json。"
            except OSError as remove_exc:
                residual_note = f"残留输出文件清理失败：{remove_exc}"

        detail_parts = [
            f"命令在 {CRAWL_TIMEOUT} 秒内未运行结束，子进程已被强制终止。",
            f"执行命令：{' '.join(command)}",
        ]
        if exc.stdout:
            detail_parts.append(f"--- timeout stdout ---\n{_stream_to_text(exc.stdout)}")
        if exc.stderr:
            detail_parts.append(f"--- timeout stderr ---\n{_stream_to_text(exc.stderr)}")
        if residual_note:
            detail_parts.append(residual_note)

        return _error(
            STATUS_TIMEOUT,
            "采集超时：爬虫在限定时间内没有运行结束，已被强制终止，本次没有采集结果。",
            "\n".join(detail_parts),
        )
    except FileNotFoundError as exc:
        # scrapy 命令不存在 / 不在 PATH 中：子进程根本没能启动，归入子进程失败
        return _error(
            STATUS_SUBPROCESS_FAILED,
            "采集失败：无法启动 Scrapy 子进程（scrapy 命令未找到，可能未安装或不在 PATH 中）。",
            f"启动命令 {command} 失败：{exc}",
        )
    except OSError as exc:
        return _error(
            STATUS_SUBPROCESS_FAILED,
            "采集失败：启动 Scrapy 子进程时发生系统错误。",
            f"启动命令 {command} 失败：{exc}",
        )

    if result.returncode != 0:
        detail = (
            f"退出码：{result.returncode}\n"
            f"执行命令：{' '.join(command)}\n"
            f"--- stderr ---\n{_truncate(result.stderr) or '（无 stderr 输出）'}\n"
            f"--- stdout ---\n{_truncate(result.stdout) or '（无 stdout 输出）'}"
        )
        return _error(
            STATUS_SUBPROCESS_FAILED,
            f"采集失败：Scrapy 子进程异常退出（退出码 {result.returncode}），请查看下方错误详情。",
            detail,
        )

    if not os.path.exists(output_file):
        return _error(
            STATUS_NO_OUTPUT,
            "采集失败：爬虫已运行结束，但没有生成输出文件 output.json。",
            f"子进程正常退出（退出码 0），但预期输出文件不存在：{output_file}",
        )

    # 先读出原文再解析：解析失败时可以把原文片段放进 detail 辅助排查
    try:
        with open(output_file, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except OSError as exc:
        return _error(
            STATUS_NO_OUTPUT,
            "采集失败：输出文件 output.json 存在但无法读取。",
            f"读取输出文件失败：{exc}",
        )

    try:
        books = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        snippet = raw_text[:500]
        return _error(
            STATUS_JSON_DECODE_ERROR,
            "采集失败：输出文件 output.json 的内容不是合法 JSON，无法解析采集结果。",
            f"JSON 解析错误：{exc}\n"
            f"输出文件：{output_file}\n"
            f"--- 文件内容片段（前 500 个字符）---\n{snippet}",
        )

    # 成功：保持原有书籍列表结构，供 index.html 按条目渲染
    return books
