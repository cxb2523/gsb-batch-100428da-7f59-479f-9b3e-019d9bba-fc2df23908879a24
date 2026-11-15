"""Acceptance checks for the Gupy/Telegram script using local fake endpoints."""
import importlib.util
import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PATH = os.path.join(REPO, "main.py")
TOKEN = "TESTTOKEN"


class State:
    def __init__(self):
        self.pages = []          # list of lists of jobs, index = page
        self.gupy_offsets = []
        self.telegram_calls = []  # dicts text/link
        self.fail_if_text_contains = set()


STATE = State()


def job(link, name=None):
    return {
        "jobUrl": link,
        "name": name or f"Vaga {link}",
        "careerPageName": "Empresa Teste",
        "city": "Rio de Janeiro",
        "state": "Rio de Janeiro",
        "workplaceType": "remote",
        "type": "vacancy_type_effective",
        "disabilities": False,
        "publishedDate": "2026-11-10T12:00:00.000Z",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/jobs":
            qs = parse_qs(parsed.query)
            offset = int(qs.get("offset", ["0"])[0])
            STATE.gupy_offsets.append(offset)
            page_index = offset // 10
            if page_index >= len(STATE.pages):
                self._send(200, {"data": []})
            else:
                self._send(200, {"data": STATE.pages[page_index]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.endswith("/sendMessage"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}
            text = payload.get("text", "")
            STATE.telegram_calls.append(text)
            if any(marker in text for marker in STATE.fail_if_text_contains):
                self._send(500, {"ok": False, "description": "forced failure"})
            else:
                self._send(200, {"ok": True, "result": {"message_id": 1}})
        else:
            self._send(404, {"error": "not found"})


def start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def load_main(port, db_path, limite_velhas=20, pagina_maxima=35, telegram_base=None):
    os.environ["TELEGRAM_TOKEN"] = TOKEN
    os.environ["CHAT_ID_GRUPO"] = "12345"
    os.environ["GUPY_DB_PATH"] = db_path
    os.environ["GUPY_API_URL"] = f"http://127.0.0.1:{port}/api/v1/jobs"
    os.environ["TELEGRAM_API_BASE"] = telegram_base or f"http://127.0.0.1:{port}"
    os.environ["INTERVALO_ENVIO"] = "0"
    os.environ["GUPY_LIMITE_VELHAS"] = str(limite_velhas)
    os.environ["GUPY_PAGINA_MAXIMA"] = str(pagina_maxima)

    spec = importlib.util.spec_from_file_location("gupy_main", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reset_state():
    STATE.pages = []
    STATE.gupy_offsets = []
    STATE.telegram_calls = []
    STATE.fail_if_text_contains = set()


def db_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT link, titulo, status FROM vagas_enviadas ORDER BY link"
        ).fetchall()
    finally:
        conn.close()


def telegram_ok_links():
    return [t for t in STATE.telegram_calls if "forced failure" not in t]


def check(name, condition, detail=""):
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} {detail}")
        raise AssertionError(name)


def test_failure_then_retry(tmpdir, port):
    reset_state()
    db = os.path.join(tmpdir, "retry.db")
    jobs_p1 = [job("https://x/a"), job("https://x/b")]
    STATE.pages = [jobs_p1]
    STATE.fail_if_text_contains = {"a", "b"}  # 两条都发送失败

    main = load_main(port, db)
    main.buscar_vagas_gupy()
    rows = db_rows(db)
    check("run1: jobs stored", len(rows) == 2, rows)
    check("run1: all pending", all(r[2] == "pending" for r in rows), rows)

    # 第二次运行：Telegram 恢复正常，待发送补发，Gupy 仍返回同样岗位
    STATE.fail_if_text_contains = set()
    STATE.telegram_calls = []
    main = load_main(port, db)
    main.buscar_vagas_gupy()
    rows = db_rows(db)
    check("run2: all sent", all(r[2] == "sent" for r in rows), rows)
    check("run2: each link sent exactly once",
          len([t for t in STATE.telegram_calls if "Clique aqui" in t]) == 2,
          STATE.telegram_calls)
    check("run2: no duplicate rows", len(rows) == 2, rows)


def test_migration(tmpdir, port):
    reset_state()
    db = os.path.join(tmpdir, "legacy.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE vagas_enviadas (link TEXT PRIMARY KEY, data_publicacao TEXT, titulo TEXT)"
    )
    conn.execute(
        "INSERT INTO vagas_enviadas VALUES ('https://x/old1', '01/01/2020', 'Old 1')"
    )
    conn.execute(
        "INSERT INTO vagas_enviadas VALUES ('https://x/old2', '02/01/2020', 'Old 2')"
    )
    conn.commit()
    conn.close()

    STATE.pages = [[job("https://x/old1"), job("https://x/old2"), job("https://x/new")]]

    main = load_main(port, db, limite_velhas=20)
    main.buscar_vagas_gupy()

    rows = db_rows(db)
    check("migration: all three rows kept", len(rows) == 3, rows)
    old_rows = [r for r in rows if r[0] in ("https://x/old1", "https://x/old2")]
    check("migration: legacy rows marked sent",
          all(r[2] == "sent" for r in old_rows), old_rows)
    check("migration: old links not posted",
          not any("Old 1" in t or "Old 2" in t for t in STATE.telegram_calls),
          STATE.telegram_calls)
    check("migration: new link posted once",
          len([t for t in STATE.telegram_calls if "Vaga https://x/new" in t]) == 1,
          STATE.telegram_calls)
    new_row = [r for r in rows if r[0] == "https://x/new"]
    check("migration: new row sent", new_row and new_row[0][2] == "sent", new_row)


def test_threshold_stops_all_pages(tmpdir, port):
    reset_state()
    db = os.path.join(tmpdir, "threshold.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE vagas_enviadas (link TEXT PRIMARY KEY, data_publicacao TEXT, titulo TEXT)"
    )
    for i in range(2):
        conn.execute(
            "INSERT INTO vagas_enviadas VALUES (?, '01/01/2020', ?)",
            (f"https://x/old{i}", f"Old {i}")
        )
    conn.commit()
    conn.close()

    STATE.pages = [
        [job("https://x/old0"), job("https://x/old1"), job("https://x/new")],
        [job("https://x/never1")],
        [job("https://x/never2")],
    ]

    main = load_main(port, db, limite_velhas=2, pagina_maxima=5)
    main.buscar_vagas_gupy()

    # 两个 filtros 都只应请求 offset=0
    check("threshold: only first page requested", set(STATE.gupy_offsets) == {0},
          STATE.gupy_offsets)
    check("threshold: no later-page job stored",
          not any(r[0] in ("https://x/never1", "https://x/never2") for r in db_rows(db)),
          db_rows(db))


def test_resend_failure_does_not_block_new(tmpdir, port):
    reset_state()
    db = os.path.join(tmpdir, "block.db")
    STATE.pages = [[job("https://x/a")]]
    STATE.fail_if_text_contains = {"Vaga https://x/a"}

    main = load_main(port, db)
    main.buscar_vagas_gupy()
    rows = db_rows(db)
    check("block run1: a pending", len(rows) == 1 and rows[0][2] == "pending", rows)

    # 第二轮：A 的补发继续失败，但新岗位 B 必须能正常发送
    STATE.pages = [[job("https://x/a"), job("https://x/b")]]
    STATE.telegram_calls = []
    main = load_main(port, db)
    main.buscar_vagas_gupy()
    rows = db_rows(db)
    status = {r[0]: r[2] for r in rows}
    check("block: a still pending", status.get("https://x/a") == "pending", status)
    check("block: b sent", status.get("https://x/b") == "sent", status)
    check("block: b posted once",
          len([t for t in STATE.telegram_calls if "Vaga https://x/b" in t]) == 1,
          STATE.telegram_calls)


def test_duplicate_within_run(tmpdir, port):
    reset_state()
    db = os.path.join(tmpdir, "dupe.db")
    STATE.pages = [
        [job("https://x/dup")],
        [job("https://x/dup")],
    ]
    main = load_main(port, db)
    main.buscar_vagas_gupy()
    rows = db_rows(db)
    check("dupe: one row only", len(rows) == 1, rows)
    check("dupe: one successful post",
          len([t for t in STATE.telegram_calls if "Vaga https://x/dup" in t]) == 1,
          STATE.telegram_calls)


def test_request_exception_then_retry(tmpdir, port):
    """Telegram 端点不可达（请求异常）时也必须保留 pending，并在下一轮补发。"""
    reset_state()
    db = os.path.join(tmpdir, "exception.db")
    STATE.pages = [[job("https://x/conn")]]

    # 这一轮把 Telegram 指向一个已关闭的端口，制造连接异常。
    import socket
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()

    main = load_main(port, db, telegram_base=f"http://127.0.0.1:{dead_port}")
    main.buscar_vagas_gupy()
    rows = db_rows(db)
    check("exception: stored as pending", len(rows) == 1 and rows[0][2] == "pending", rows)

    # 下一轮恢复真实端点，应补发成功且只成功一次。
    STATE.telegram_calls = []
    main = load_main(port, db)
    main.buscar_vagas_gupy()
    rows = db_rows(db)
    status = {r[0]: r[2] for r in rows}
    check("exception: resent to sent", status.get("https://x/conn") == "sent", status)
    check("exception: posted once",
          len([t for t in STATE.telegram_calls if "Vaga https://x/conn" in t]) == 1,
          STATE.telegram_calls)


def test_pending_not_counted_as_old(tmpdir, port):
    """待发送记录不应被当成"旧岗位"而提前终止分页。"""
    reset_state()
    db = os.path.join(tmpdir, "pending_old.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE vagas_enviadas (link TEXT PRIMARY KEY, data_publicacao TEXT, "
        "titulo TEXT, status TEXT, mensagem TEXT, atualizado_em TEXT)"
    )
    for i in range(3):
        conn.execute(
            "INSERT INTO vagas_enviadas VALUES (?, '01/01/2020', ?, 'pending', NULL, NULL)",
            (f"https://x/pend{i}", f"Pend {i}")
        )
    conn.commit()
    conn.close()

    # 这些 pending 补发仍失败，因此本轮扫描时它们依然是 pending。
    # 阈值设为 1：若 pending 被算作 old，第一页就会终止，第二页不会被请求。
    STATE.fail_if_text_contains = {"Pend "}
    STATE.pages = [
        [job("https://x/pend0"), job("https://x/pend1"), job("https://x/pend2")],
        [job("https://x/later")],
    ]
    main = load_main(port, db, limite_velhas=1, pagina_maxima=5)
    main.buscar_vagas_gupy()

    check("pending_old: later page requested", 10 in STATE.gupy_offsets, STATE.gupy_offsets)
    status = {r[0]: r[2] for r in db_rows(db)}
    check("pending_old: later job sent", status.get("https://x/later") == "sent", status)
    check("pending_old: pendings still pending",
          all(status.get(f"https://x/pend{i}") == "pending" for i in range(3)), status)


def main():
    server, port = start_server()
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_failure_then_retry(tmpdir, port)
            test_request_exception_then_retry(tmpdir, port)
            test_migration(tmpdir, port)
            test_threshold_stops_all_pages(tmpdir, port)
            test_resend_failure_does_not_block_new(tmpdir, port)
            test_duplicate_within_run(tmpdir, port)
            test_pending_not_counted_as_old(tmpdir, port)
    finally:
        server.shutdown()
    print("\nALL ACCEPTANCE CHECKS PASSED")


if __name__ == "__main__":
    main()
