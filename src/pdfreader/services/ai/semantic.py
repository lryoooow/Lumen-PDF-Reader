"""可选向量检索，只使用用户配置的兼容服务；向量仅在任务内存中存在。"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from pdfreader.services.ai.client import AiError, ChatClient
from pdfreader.services.ai.research import ResearchChunk


def embedding_requests(chunk_count: int) -> int:
    return (chunk_count + 31) // 32 + 1


def semantic_retrieve(
    client: ChatClient,
    chunks: Sequence[ResearchChunk],
    query: str,
    *,
    model: str,
    cancelled: Callable[[], bool],
    progress: Callable[[str], None],
    limit: int = 12,
) -> list[ResearchChunk]:
    if not query.strip() or not chunks or len(chunks) > 512:
        raise AiError("向量检索需要问题和 1–512 个分片，请缩小页范围")
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), 32):
        if cancelled():
            raise AiError("已取消")
        progress(f"正在建立会话向量：{start + 1} / {len(chunks)} 个分片")
        vectors.extend(
            client.embeddings([chunk.text for chunk in chunks[start : start + 32]], model=model)
        )
    if cancelled():
        raise AiError("已取消")
    query_vector = client.embeddings([query], model=model)[0]
    if any(len(vector) != len(query_vector) for vector in vectors):
        raise AiError("向量模型在请求间返回了不同维度")

    def unit(vector: list[float]) -> list[float]:
        scale = max(abs(value) for value in vector)
        scaled = [value / scale for value in vector]
        norm = math.sqrt(sum(value * value for value in scaled))
        return [value / norm for value in scaled]

    query_unit = unit(query_vector)

    def cosine(vector: list[float]) -> float:
        return sum(left * right for left, right in zip(unit(vector), query_unit, strict=True))

    order = sorted(range(len(chunks)), key=lambda i: (-cosine(vectors[i]), i))
    return [chunks[i] for i in order[:limit]]
