# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：Boss 直聘 HR 智能招聘助手"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# 运行时依赖：playwright（含浏览器驱动）、winsdk（Windows OCR）、openpyxl（Excel）
for pkg in ("playwright", "winsdk", "openpyxl", "httpx"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# winrt 运行时（winsdk 的底层）
for pkg in ("winrt", "winrt_runtime"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += [
    "engine", "engine.rpa", "engine.ocr", "engine.matcher",
    "engine.recorder", "engine.feishu", "engine.selectors", "engine.stealth",
    "ui", "ui.main_window",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "unittest", "pydoc", "tkinter.test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BossHR助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="BossHR助手",
)
