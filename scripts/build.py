#!/usr/bin/env python3
"""Build script — runs PyInstaller and assembles the distribution folder.

Usage: python scripts/build.py

Output: dist/WeComBot/ (ready to zip and distribute)
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "WeComBot"
SPEC = ROOT / "WeComBot.spec"
BGE_MODEL_DIR = ROOT / "bge_model"


def ensure_bge_model():
    """Download BGE model if bge_model/ doesn't exist locally."""
    if (BGE_MODEL_DIR / "model.safetensors").exists():
        print("[0/5] BGE model found in bge_model/")
        return

    print("[0/5] Downloading BGE model (~92MB)...")
    from sentence_transformers import SentenceTransformer
    SentenceTransformer("BAAI/bge-small-zh-v1.5")
    # Model is cached in HF hub; copy to local bge_model/
    from huggingface_hub import snapshot_download
    snapshot_download("BAAI/bge-small-zh-v1.5", local_dir=str(BGE_MODEL_DIR))
    print("   BGE model ready")


def clean():
    """Remove previous build artifacts."""
    dirs_to_clean = [ROOT / "build", ROOT / "dist"]
    for d in dirs_to_clean:
        if d.exists():
            shutil.rmtree(d)
    print("[1/5] Cleaned old build artifacts")


def run_pyinstaller():
    """Run PyInstaller with the spec file."""
    print("[2/5] Running PyInstaller...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC),
         "--distpath", str(ROOT / "dist"),
         "--workpath", str(ROOT / "build"),
         "--noconfirm"],
        cwd=str(ROOT),
        capture_output=False,
    )
    if result.returncode != 0:
        print("\n[ERROR] PyInstaller failed!")
        sys.exit(1)
    print("   PyInstaller done")


def copy_extras():
    """Copy launcher scripts and config to the dist folder."""
    print("[3/5] Copying launcher scripts...")

    extras = [
        "启动.bat",
        "setup.bat",
        "config.json",
        ".env.example",
    ]
    for f in extras:
        src = ROOT / f
        if src.exists():
            shutil.copy2(src, DIST / f)
            print(f"   Copied: {f}")

    # Create runtime directories
    for d in ["data/chat_raw", "data/context", "data/pending",
              "data/qdrant", "data/state", "data/videos", "logs"]:
        (DIST / d).mkdir(parents=True, exist_ok=True)
    print("   Created runtime directories")


def verify():
    """Quick sanity check on the dist folder."""
    print("[4/5] Verifying...")
    required = ["启动.exe", "启动.bat", "setup.bat", "config.json", ".env.example"]
    missing = [f for f in required if not (DIST / f).exists()]
    if missing:
        print(f"\n[ERROR] Missing files in dist: {missing}")
        sys.exit(1)

    # Estimate package size
    total_size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)
    print(f"   Package size: ~{size_mb:.0f} MB")
    print(f"\n  Build complete! Output: {DIST}")
    print(f"  Zip and distribute: {DIST}.zip")


if __name__ == "__main__":
    ensure_bge_model()
    clean()
    run_pyinstaller()
    copy_extras()
    verify()
