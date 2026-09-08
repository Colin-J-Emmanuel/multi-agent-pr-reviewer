import logging
from functools import lru_cache

from fastembed import TextEmbedding

logger = logging.getLogger("pr-reviewer.embeddings")

MODEL_NAME = "BAAI/bge-small-en-v1.5"     # 384 dimensions — must match vector(384)
EMBEDDING_DIM = 384


@lru_cache
def _model() -> TextEmbedding:
    """Load the embedding model once and reuse it (first call downloads it)."""
    logger.info("Loading embedding model %s", MODEL_NAME)
    return TextEmbedding(model_name=MODEL_NAME)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one vector per input, in order."""
    if not texts:
        return []
    return [vec.tolist() for vec in _model().embed(texts)]


def finding_text(category: str, file: str, message: str) -> str:
    """The text we embed for a finding — category and file add useful context."""
    return f"[{category}] {file}: {message}"
