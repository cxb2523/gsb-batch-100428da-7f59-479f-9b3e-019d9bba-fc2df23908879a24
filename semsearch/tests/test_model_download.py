from pathlib import Path

import httpx

from semsearch.index.embedding_model import MODEL_FILES, is_model_installed
from semsearch.model_download import clean_broken_model_cache, download_embedding_model


def test_clean_broken_model_cache_removes_incomplete_local_install(tmp_path: Path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

    clean_broken_model_cache(model_dir)

    assert not model_dir.exists()


def test_clean_broken_model_cache_keeps_complete_installs(tmp_path: Path):
    model_dir = tmp_path / "model"
    for name in MODEL_FILES:
        (model_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (model_dir / name).write_text("x", encoding="utf-8")

    clean_broken_model_cache(model_dir)

    assert model_dir.exists()


def test_download_embedding_model_fetches_files_directly(tmp_path: Path, monkeypatch):
    model_dir = tmp_path / "embeddinggemma-2"
    monkeypatch.setattr("semsearch.model_download.LOCAL_MODEL_DIR", model_dir)
    monkeypatch.setattr("semsearch.index.embedding_model.LOCAL_MODEL_DIR", model_dir)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(200, content=b"data")

    real_client = httpx.Client
    monkeypatch.setattr(
        "semsearch.model_download.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    result = download_embedding_model()

    assert result == model_dir
    assert is_model_installed(model_dir)
    assert len(requested) == len(MODEL_FILES)
    assert all("authorization" not in r.headers for r in requested)
    assert not list(model_dir.rglob("*.part"))
