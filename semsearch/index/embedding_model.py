from collections.abc import Iterator
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from .embedding_config import EMBED_CHUNK_BATCH_SIZE

HF_MODEL_REPO = "onnx-community/embeddinggemma-2-ONNX"
DEFAULT_MODEL = f"{HF_MODEL_REPO}@256d"
# Matryoshka-truncated from the native 768 dims: ~3x smaller index, quality largely intact.
EMBEDDING_DIM = 256
MAX_SEQUENCE_LENGTH = 512

MODEL_CACHE_DIR = Path("data") / "models"
LOCAL_MODEL_DIR = MODEL_CACHE_DIR / "embeddinggemma-2"

# int8 text tower (~313MB); the repo also ships fp16/q4 variants and vision/audio encoders.
MODEL_FILES = (
    "onnx/model_quantized.onnx",
    "onnx/model_quantized.onnx_data",
    "tokenizer.json",
    "config.json",
)

DOCUMENT_PREFIX = "title: none | text: "
QUERY_PREFIX = "task: search result | query: "

_MULTIMODAL_INPUTS = ("image_features", "video_features", "audio_features")


class GemmaEmbedder:
    """EmbeddingGemma 2 text embedder running directly on ONNX Runtime."""

    def __init__(self, model_dir: Path):
        self._session = ort.InferenceSession(
            str(model_dir / MODEL_FILES[0]),
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tokenizer.enable_padding(pad_id=self._tokenizer.token_to_id("<pad>") or 0)
        self._tokenizer.enable_truncation(MAX_SEQUENCE_LENGTH)
        self._input_names = {i.name for i in self._session.get_inputs()}

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(texts)
        feeds = {
            "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
        }
        # The graph also accepts image/video/audio features; text-only means none of them.
        for name in _MULTIMODAL_INPUTS:
            if name in self._input_names:
                feeds[name] = np.zeros((0, 512), dtype=np.float32)
        embeddings = self._session.run(["sentence_embedding"], feeds)[0][:, :EMBEDDING_DIM]
        return embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

    def _embed(self, texts: list[str], prefix: str, batch_size: int) -> Iterator[np.ndarray]:
        prefixed = [prefix + text for text in texts]
        # Length-sorted batches minimise padding; yield back in input order.
        order = sorted(range(len(prefixed)), key=lambda i: len(prefixed[i]))
        results: list[np.ndarray | None] = [None] * len(prefixed)
        for start in range(0, len(order), batch_size):
            idx = order[start : start + batch_size]
            for i, vec in zip(idx, self._embed_batch([prefixed[i] for i in idx]), strict=True):
                results[i] = vec
        yield from results  # type: ignore[misc]

    def embed(self, chunks: list[str], *, batch_size: int = EMBED_CHUNK_BATCH_SIZE, **_ignored) -> Iterator[np.ndarray]:
        """Embed document chunks."""
        return self._embed(list(chunks), DOCUMENT_PREFIX, batch_size)

    def embed_query(self, query: str) -> np.ndarray:
        return next(self._embed([query], QUERY_PREFIX, 1))


_embedder: GemmaEmbedder | None = None


def is_model_installed(model_dir: Path | None = None) -> bool:
    target = LOCAL_MODEL_DIR if model_dir is None else model_dir
    return all((target / name).exists() for name in MODEL_FILES)


def load_embedder(
    *,
    model_name: str = DEFAULT_MODEL,
    force_reload: bool = False,
) -> GemmaEmbedder:
    global _embedder

    if model_name != DEFAULT_MODEL:
        raise ValueError(f"Unsupported embedding model: {model_name}")
    if _embedder is not None and not force_reload:
        return _embedder

    _embedder = GemmaEmbedder(LOCAL_MODEL_DIR)
    return _embedder
