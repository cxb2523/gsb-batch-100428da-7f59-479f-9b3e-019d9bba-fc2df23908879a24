"""Token-efficient semantic web retrieval for AI agents."""

from .config import Settings
from .indexer import Indexer
from .models import (
    Document,
    PageOutcome,
    PipelineFailure,
    SearchHit,
    SearchResponse,
    SearchResult,
)
from .pipeline import SearchPipeline
from .retrieval import Retriever

__all__ = [
    "Document",
    "Indexer",
    "PageOutcome",
    "PipelineFailure",
    "Retriever",
    "SearchHit",
    "SearchPipeline",
    "SearchResponse",
    "SearchResult",
    "Settings",
]
__version__ = "0.2.0"
