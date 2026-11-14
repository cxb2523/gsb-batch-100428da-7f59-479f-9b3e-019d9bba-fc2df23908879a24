import os
import shutil
from pathlib import Path

import httpx

from .index.embedding_model import (
    HF_MODEL_REPO,
    LOCAL_MODEL_DIR,
    MODEL_FILES,
    is_model_installed,
)

HF_RESOLVE_URL = "https://huggingface.co/{repo}/resolve/main/{path}"


def clean_broken_model_cache(model_dir: Path = LOCAL_MODEL_DIR) -> None:
    """Remove incomplete model directories left by interrupted downloads."""
    if model_dir.exists() and not is_model_installed(model_dir):
        shutil.rmtree(model_dir, ignore_errors=True)


def _download_file(client: httpx.Client, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for block in response.iter_bytes(1 << 20):
                handle.write(block)
    partial.replace(dest)


def download_embedding_model(*, force: bool = False) -> Path:
    """Download the ONNX embedding model files directly; no account or token required."""
    LOCAL_MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    if is_model_installed() and not force:
        return LOCAL_MODEL_DIR

    if force and LOCAL_MODEL_DIR.exists():
        shutil.rmtree(LOCAL_MODEL_DIR, ignore_errors=True)

    headers = {}
    if token := os.environ.get("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(follow_redirects=True, timeout=600, headers=headers) as client:
        for name in MODEL_FILES:
            dest = LOCAL_MODEL_DIR / name
            if dest.exists():
                continue
            _download_file(client, HF_RESOLVE_URL.format(repo=HF_MODEL_REPO, path=name), dest)

    if not is_model_installed():
        clean_broken_model_cache(LOCAL_MODEL_DIR)
        raise RuntimeError(
            f"Downloaded model at {LOCAL_MODEL_DIR} is incomplete. "
            "Check your network connection and retry `uv run setup-models`."
        )

    return LOCAL_MODEL_DIR
