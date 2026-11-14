import numpy as np
import pytest

from semsearch.index.embedding_model import DEFAULT_MODEL
from semsearch.index.embeddings import EmbeddingIndex
from semsearch.search.semantic import semantic_scores


def test_semantic_scores_picks_best_chunk_per_document(monkeypatch):
    embedding_index = EmbeddingIndex.from_vectors(
        model_name=DEFAULT_MODEL,
        chunk_doc_ids=["doc-a", "doc-a", "doc-b"],
        vectors=np.asarray(
            [
                [1.0, 0.0],
                [0.24253563, 0.9701425],
                [0.0, 1.0],
            ],
            dtype=np.float32,
        ),
    )
    monkeypatch.setattr(
        "semsearch.search.semantic.embed_query",
        lambda query, model_name=DEFAULT_MODEL: np.asarray([0.0, 1.0], dtype=np.float32),
    )

    scores = semantic_scores("example", embedding_index, ["doc-a", "doc-b", "doc-c"])

    assert scores["doc-a"] == pytest.approx(0.9701425, abs=0.02)
    assert scores["doc-b"] == pytest.approx(1.0, abs=0.02)
    assert "doc-c" not in scores


def test_semantic_scores_ignores_index_from_other_model():
    embedding_index = EmbeddingIndex.from_vectors(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        chunk_doc_ids=["doc-a"],
        vectors=np.asarray([[1.0, 0.0]], dtype=np.float32),
    )

    assert semantic_scores("q", embedding_index, ["doc-a"]) == {}
