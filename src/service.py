# -*- coding: utf-8 -*-
"""FastAPI 服务：网页里点按钮下载番茄小说，实时看进度，下载成品 TXT。

单任务模型（浏览器是共享单例）。下载在后台线程跑，前端轮询 /api/status。

启动（两种方式等价）：
  cd src
  python service.py                                    # 直接跑，最省事
  uvicorn service:app --host 127.0.0.1 --port 8000     # 开发/部署更灵活
然后浏览器打开 http://127.0.0.1:8000
（前提：专用 Chrome 已用 launch_chrome 起好并登录，调试端口 9333 开着。）

python service.py 支持环境变量覆盖 host/port：
  set HOST=0.0.0.0 & set PORT=9000 & python service.py
"""
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from config import OUTPUT_DIR
from task_manager import TaskManager

app = FastAPI(title='番茄小说 CDP 下载器')
manager = TaskManager()

_WEBAPP_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'webapp')
_INDEX_HTML = os.path.join(_WEBAPP_DIR, 'index.html')


class StartRequest(BaseModel):
    novel: Optional[str] = None
    attach: bool = False
    start: Optional[int] = None
    end: Optional[int] = None
    split: bool = True


@app.get('/', response_class=HTMLResponse)
def index() -> HTMLResponse:
    with open(_INDEX_HTML, 'r', encoding='UTF-8') as f:
        return HTMLResponse(f.read())


@app.post('/api/start')
def api_start(req: StartRequest) -> JSONResponse:
    if not req.attach and not req.novel:
        return JSONResponse(
            {'ok': False, 'message': '请填写小说ID/链接，或勾选“接管当前页”。'},
            status_code=400)
    ok, msg = manager.start(
        novel=req.novel, attach=req.attach, start=req.start,
        end=req.end, split=req.split)
    return JSONResponse({'ok': ok, 'message': msg},
                        status_code=200 if ok else 409)


@app.post('/api/stop')
def api_stop() -> JSONResponse:
    ok, msg = manager.stop()
    return JSONResponse({'ok': ok, 'message': msg})


@app.get('/api/status')
def api_status() -> JSONResponse:
    return JSONResponse(manager.snapshot())


@app.get('/api/books')
def api_books() -> JSONResponse:
    """列出 output 下已下载的书（有单文件 TXT 的目录）。"""
    books = []
    if os.path.isdir(OUTPUT_DIR):
        for name in sorted(os.listdir(OUTPUT_DIR)):
            d = os.path.join(OUTPUT_DIR, name)
            if not os.path.isdir(d):
                continue
            txts = [f for f in os.listdir(d) if f.endswith('.txt')]
            if txts:
                txt = txts[0]
                size = os.path.getsize(os.path.join(d, txt))
                books.append({'dir': name, 'txt': txt,
                              'size_mb': round(size / 1024 / 1024, 2)})
    return JSONResponse({'books': books})


@app.get('/api/download/{book_dir}')
def api_download(book_dir: str) -> FileResponse:
    """下载某本书的单文件 TXT。book_dir 为 output 下的目录名。"""
    # 防目录穿越：只允许 output 下的直接子目录名。
    safe = os.path.basename(book_dir)
    d = os.path.join(OUTPUT_DIR, safe)
    if not os.path.isdir(d):
        return JSONResponse({'ok': False, 'message': '未找到该书。'},
                            status_code=404)
    txts = [f for f in os.listdir(d) if f.endswith('.txt')]
    if not txts:
        return JSONResponse({'ok': False, 'message': '该书暂无 TXT。'},
                            status_code=404)
    path = os.path.join(d, txts[0])
    return FileResponse(path, filename=txts[0],
                        media_type='text/plain; charset=utf-8')


def main() -> None:
    """直接启动服务：python service.py。host/port 可用环境变量覆盖。"""
    import uvicorn
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8000'))
    print(f'番茄下载器服务启动中 → http://{host}:{port}')
    print('（请确认专用 Chrome 已用 launch_chrome 起好并登录，端口 9333）')
    # 传 app 对象而非 "service:app" 字符串，这样 python service.py 直接可跑；
    # 需要 --reload 热重载时用 uvicorn 命令行方式。
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    main()
