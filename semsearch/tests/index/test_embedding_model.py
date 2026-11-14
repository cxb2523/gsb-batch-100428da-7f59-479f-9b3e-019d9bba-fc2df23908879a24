from pathlib import Path

from semsearch.index.embedding_model import MODEL_FILES, is_model_installed


def test_is_model_installed_requires_all_model_files(tmp_path: Path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    assert not is_model_installed(model_dir)

    for name in MODEL_FILES[:-1]:
        (model_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (model_dir / name).write_text("x", encoding="utf-8")
    assert not is_model_installed(model_dir)

    (model_dir / MODEL_FILES[-1]).write_text("x", encoding="utf-8")
    assert is_model_installed(model_dir)
