# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WeComBot — 企业微信智能客服一键打包."""

import sys
from pathlib import Path
import importlib.metadata
import Cython
import paddle
import paddleocr

# ── Hidden imports that PyInstaller can't auto-detect ──────────────
# torch, paddle, and sentence_transformers use dynamic imports
# that PyInstaller's analysis misses.

hiddenimports = [
    # PyTorch + CUDA (CPU-only build, but keep for safety)
    "torch",
    "torch.nn",
    "torch.utils",
    "torch._C",
    # PaddleOCR + PaddlePaddle
    "paddle",
    "paddleocr",
    "paddleocr.ppocr",
    "paddleocr.ppstructure",
    "paddleocr.tools",
    "paddleocr.tools.infer",
    # Sentence Transformers (BGE model)
    "sentence_transformers",
    "sentence_transformers.models",
    "transformers",
    "transformers.models.bert",
    # Qdrant
    "qdrant_client",
    "qdrant_client.http",
    # OpenAI (DeepSeek API)
    "openai",
    # Image processing
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    # Windows automation
    "pynput",
    "pynput.keyboard",
    "pynput.mouse",
    "win32gui",
    "win32con",
    "win32process",
    "win32api",
    "win32clipboard",
    # Screenshot
    "mss",
    "mss.windows",
    # Misc
    "psutil",
    "numpy",
    "dotenv",
    "asyncio",
    "queue",
    "json",
    "logging",
    "dataclasses",
    "ctypes",
]

# ── Data files to include ──────────────────────────────────────────
# These are copied into the bundle root so the exe can find them.
_CYTHON_UTILITY = Path(Cython.__file__).parent / "Utility"
_PADDLEOCR_DIR = Path(paddleocr.__file__).parent
_IMAGEIO_DIST_INFO = Path(importlib.metadata.distribution("imageio")._path)
_PADDLE_LIBS = Path(paddle.__file__).parent / "libs"

datas = [
    ("config.json", "."),
    (".env.example", "."),
    ("data", "data"),                                 # runtime data dirs
    ("bge_model", "bge_model"),                       # BGE model (~92MB, offline)
    (str(_CYTHON_UTILITY), "Cython/Utility"),         # Cython cpp templates (PaddleOCR needs)
    (str(_IMAGEIO_DIST_INFO), _IMAGEIO_DIST_INFO.name),  # imageio metadata (imgaug needs at import)
    # PaddleOCR dynamic imports — PyInstaller can't trace spec_from_file_location.
    # Also at root level: paddleocr/paddleocr.py has same name as package, so
    # PyInstaller resolves __file__ to _MEI*/paddleocr.py (root), not the nested
    # paddleocr/paddleocr.py. Thus os.path.dirname(__file__) → root of temp dir.
    (str(_PADDLEOCR_DIR / "tools"), "tools"),
    (str(_PADDLEOCR_DIR / "tools"), "paddleocr/tools"),
    (str(_PADDLEOCR_DIR / "ppocr"), "ppocr"),
    (str(_PADDLEOCR_DIR / "ppocr"), "paddleocr/ppocr"),
    (str(_PADDLEOCR_DIR / "ppstructure"), "ppstructure"),
    (str(_PADDLEOCR_DIR / "ppstructure"), "paddleocr/ppstructure"),
]

# PaddlePaddle runtime DLLs (mklml, mkldnn, etc.)
paddle_dlls = [
    (str(dll), ".") for dll in _PADDLE_LIBS.glob("*.dll")
]

# ── Exclude dirs that should NOT be bundled ─────────────────────────
excluded_dirs = [
    "tests",
    "docs",
    "eval",
    ".git",
    "__pycache__",
    "scripts",
    "logs",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=paddle_dlls,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_dirs,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="启动",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # Windows GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WeComBot",
)
