import os

# Tuned for ~8GB laptops: keep ONNX data-parallel off (each worker loads a model copy).
# Reuse one extract process pool for the whole run; EXTRACT_PAGE_BATCH is the in-flight window.
EXTRACT_WORKERS = min(4, os.cpu_count() or 1)
EXTRACT_PAGE_BATCH = 64
EXTRACT_POOL_RECYCLE = 100
MAX_CHUNKS_PER_DOC = 128
EMBED_CHUNK_BUDGET = 256
EMBED_SOLO_DOC_CHUNKS = MAX_CHUNKS_PER_DOC
# Small batches: on CPU they are as fast as large ones, but peak RSS is ~2.5x lower (batch 32 ~2.8GB, 8 ~1.1GB).
EMBED_CHUNK_BATCH_SIZE = 8
EMBED_PARALLEL: int | None = None
EMBED_WAIT_TIMEOUT_SEC = 0.1
