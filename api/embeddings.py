"""Embedding providers.

Two hosted providers plus a deterministic local fallback. The fallback exists
so the stack boots, the schema applies and the evals run without an API key —
it is a hashing bag-of-words, so it measures lexical overlap, not meaning.
Anything it produces is labelled as such in results.json.

中文说明：提供 OpenAI、Gemini 两个托管嵌入器，Ollama 本地嵌入器，以及
确定性的 hashing 兜底。hashing 只测量字面重叠，不代表语义；它用于在没有
API key 时启动系统和运行评测，结果会明确标注来源。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Protocol

import httpx

from .settings import Settings

# Matches either a run of ASCII word characters (for English/pinyin-style
# tokens) or a single CJK Unified Ideograph (U+4E00-U+9FFF, "一-鿿"), since
# Chinese text has no whitespace between words.
# 中文：匹配连续的 ASCII 单词或单个 CJK 字符，因为中文没有空格分词。
_TOKEN_RE = re.compile(r"[a-z0-9_]+|[一-鿿]")


class Embedder(Protocol):
    """Structural interface every embedding provider below implements.

    中文：所有嵌入器共同遵循的结构化接口。
    """

    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each input text and preserve input order.

        中文：为每段输入文本生成向量，并保持输入顺序。
        """
        ...

    async def aclose(self) -> None:
        """Release any transport the provider holds open.

        中文：释放该提供方持有的网络连接。
        """
        ...


class _SharedHttpClient:
    """One reused httpx client per embedder instance.

    Opening a client per call costs a fresh TCP connection every time, and for
    the hosted providers a fresh TLS handshake on top of it. Measured against
    local ollama on 2026-09-01, reusing the connection took a single-query
    embedding from 63.8 ms to 47.4 ms at P50 — a quarter of the call was setup,
    not inference. The read path embeds one query per agent turn, so that setup
    was being paid on every turn.

    中文：每次调用都新建 HTTP 客户端会重复建立连接；实测本地 ollama 单条
    嵌入从 63.8ms 降到 47.4ms（P50）。检索路径每轮只嵌入一条查询，这部分
    开销原本每轮都要付。
    """

    def __init__(self, timeout: float) -> None:
        """Record the timeout; the client itself is created on first use.

        中文：仅记录超时时间，客户端在首次使用时创建。
        """
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> httpx.AsyncClient:
        """Return the shared client, creating it once under a lock.

        中文：返回共享客户端；首次调用时在锁内创建，避免并发重复建连。
        """
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Close the client if one was ever opened.

        中文：若已创建客户端则关闭它。
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _l2_normalise(vector: list[float]) -> list[float]:
    """Scale a vector to unit length so cosine similarity reduces to a dot product.

    中文：将向量缩放为单位长度，这样余弦相似度就可以直接用点积计算，
    无需在查询时再做除法。

    Args:
        vector: Raw embedding vector.

    Returns:
        A unit vector, or a stable fallback when the input is all zeros.
    """
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        # A zero vector has undefined cosine similarity; nudge it so pgvector
        # returns a defined distance instead of NaN.
        # 中文：零向量的余弦相似度未定义；这里返回稳定向量，使 pgvector
        # 返回一个明确的距离值而不是 NaN。
        return [1.0] + [0.0] * (len(vector) - 1)
    return [component / norm for component in vector]


class HashingEmbedder:
    """Deterministic, offline, lexical-only. Not a semantic model.

    Tokens are hashed into buckets with a signed weight, which makes cosine
    similarity approximate token overlap. Good enough to exercise the dedup and
    decay code paths; useless for judging retrieval quality.

    中文：确定性、离线、只测量字面重叠的兜底方案，不是语义模型。它足以
    跑通去重和衰减代码路径，但不能用于评价检索质量，尤其不适合跨语言文本。
    """

    name = "hashing-local"

    def __init__(self, dim: int) -> None:
        """Store the target vector dimension.

        中文：保存目标向量维度。
        """
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each text independently through hashing.

        中文：独立地对每段文本进行哈希编码。
        """
        return [self._embed_one(text) for text in texts]

    async def aclose(self) -> None:
        """Nothing to release; the fallback holds no connection.

        中文：兜底实现不持有连接，无需释放。
        """

    def _embed_one(self, text: str) -> list[float]:
        """Hash one text's tokens into a signed, L2-normalised bag-of-words vector.

        中文：把 token 哈希进带正负权重的桶位并做 L2 归一化；结果是只反映
        字面重叠的词袋向量。

        Args:
            text: Raw text to embed.

        Returns:
            An L2-normalised vector of the configured width.
        """
        vector = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        return _l2_normalise(vector)


class OpenAIEmbedder:
    """Hosted embeddings via the OpenAI embeddings API.

    中文：通过 OpenAI 的 embeddings 接口获取云端向量嵌入。
    """

    name = "openai"

    def __init__(self, settings: Settings) -> None:
        """Validate the API key and cache the model/timeout to use.

        中文：校验 API key，并保存模型名与超时时间。

        Raises:
            ValueError: If no OpenAI API key is configured.
        """
        if not settings.openai_api_key:
            raise ValueError(
                "MINDBRIDGE_EMBEDDING_PROVIDER=openai requires "
                "MINDBRIDGE_OPENAI_API_KEY"
            )
        self.dim = settings.embedding_dim
        self._model = settings.embedding_model
        self._key = settings.openai_api_key
        self._http = _SharedHttpClient(settings.embedding_timeout_seconds)

    async def aclose(self) -> None:
        """Close the shared HTTP connection to OpenAI.

        中文：关闭与 OpenAI 之间的共享 HTTP 连接。
        """
        await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Call the OpenAI embeddings endpoint and return vectors in input order.

        中文：调用 OpenAI embeddings 接口，并按输入顺序返回向量。

        Args:
            texts: Texts to embed.

        Returns:
            One vector per input text, in the same order.
        """
        client = await self._http.get()
        response = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self._key}"},
            json={"model": self._model, "input": texts, "dimensions": self.dim},
        )
        response.raise_for_status()
        payload = response.json()
        # The API may return items out of order; re-sort by the index it echoes
        # back so the returned vectors line up with the input `texts` list.
        # 中文：接口可能乱序返回；按回传 index 排序，使向量与输入一一对应。
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]


class GeminiEmbedder:
    """Hosted embeddings via Google's Gemini batchEmbedContents API.

    中文：通过 Google Gemini 的 batchEmbedContents 接口获取云端向量嵌入。
    """

    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        """Validate the API key and cache the model/timeout to use.

        中文：校验 API key，并保存模型名与超时时间。

        Raises:
            ValueError: If no Gemini API key is configured.
        """
        if not settings.gemini_api_key:
            raise ValueError(
                "MINDBRIDGE_EMBEDDING_PROVIDER=gemini requires "
                "MINDBRIDGE_GEMINI_API_KEY"
            )
        self.dim = settings.embedding_dim
        self._model = settings.embedding_model
        self._key = settings.gemini_api_key
        self._http = _SharedHttpClient(settings.embedding_timeout_seconds)

    async def aclose(self) -> None:
        """Close the shared HTTP connection to Gemini.

        中文：关闭与 Gemini 之间的共享 HTTP 连接。
        """
        await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Call Gemini's batch embedding endpoint and return vectors in input order.

        中文：调用 Gemini 批量嵌入接口，并按输入顺序返回向量。

        Args:
            texts: Texts to embed.

        Returns:
            One vector per input text, in the same order.
        """
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self._model}:batchEmbedContents"
        )
        requests = [
            {
                "model": f"models/{self._model}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self.dim,
            }
            for text in texts
        ]
        client = await self._http.get()
        response = await client.post(
            url,
            headers={"x-goog-api-key": self._key},
            json={"requests": requests},
        )
        response.raise_for_status()
        payload = response.json()
        return [item["values"] for item in payload["embeddings"]]


class OllamaEmbedder:
    """Local semantic embeddings through ollama.

    The reason this exists: the hashing fallback measures token overlap, so it
    scores real duplicates 0.13-0.73 — "Use uv instead of pip" against
    "Python 项目优先用 uv" lands at 0.25 because they share almost no tokens.
    No threshold separates duplicates from unrelated preferences, so write-time
    dedup silently never fires and T3 fills with paraphrases of one fact.

    ``nomic-embed-text`` supplies the semantic signal that hashing lacks, but
    the 170-row evaluation showed that scores from 0.62 through 0.75 are mostly
    topical similarities, not duplicates. The default 0.80 threshold therefore
    favors avoiding false merges and must be retuned for another model.

    Unlike the hosted embedders this keeps the local-only promise intact, which
    matters because embeddings are computed on every preference write.

    中文：hashing 只能衡量词元重叠，跨语言重复语句会得到很低的分数，无法
    选择安全阈值。nomic-embed-text 能提供语义信号，但 170 条数据的评估表明，
    0.62 到 0.75 的相似项大多只是主题相关，并非重复事实。因此默认阈值采用
    0.80 以减少错误合并；更换模型后必须重新校准。整个过程仍保留在本地。
    """

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        """Store the model, base URL, and timeout.

        中文：保存模型名、基础 URL 和超时时间。
        """
        self.dim = settings.embedding_dim
        self._model = settings.embedding_model
        self._url = str(settings.ollama_url).rstrip("/")
        self._http = _SharedHttpClient(settings.embedding_timeout_seconds)

    async def aclose(self) -> None:
        """Close the shared HTTP connection to the local ollama server.

        中文：关闭与本地 ollama 服务之间的共享 HTTP 连接。
        """
        await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Call ollama's local /api/embed endpoint and normalise the results.

        中文：调用本地 Ollama 的 /api/embed 接口，并归一化结果。

        Args:
            texts: Texts to embed.

        Returns:
            One L2-normalised vector per input text, in input order.

        Raises:
            ValueError: If the model output width differs from configuration.
        """
        client = await self._http.get()
        response = await client.post(
            f"{self._url}/api/embed",
            json={"model": self._model, "input": texts},
        )
        response.raise_for_status()
        payload = response.json()
        vectors = payload["embeddings"]
        if vectors and len(vectors[0]) != self.dim:
            raise ValueError(
                f"{self._model} returned {len(vectors[0])}-dim vectors but "
                f"MINDBRIDGE_EMBEDDING_DIM is {self.dim}"
            )
        # Normalised so pgvector's cosine distance and a plain dot product agree.
        # 中文：归一化后，pgvector 余弦距离和普通点积使用同一尺度。
        return [_l2_normalise(vector) for vector in vectors]


def build_embedder(settings: Settings) -> Embedder:
    """Construct the embedder selected by settings.embedding_provider.

    中文：根据 settings.embedding_provider 构造对应的嵌入器。

    Args:
        settings: Provider selection and provider-specific configuration.

    Returns:
        The selected Embedder implementation, with hashing as the fallback.
    """
    if settings.embedding_provider == "ollama":
        return OllamaEmbedder(settings)
    if settings.embedding_provider == "openai":
        return OpenAIEmbedder(settings)
    if settings.embedding_provider == "gemini":
        return GeminiEmbedder(settings)
    return HashingEmbedder(settings.embedding_dim)


def to_pgvector(vector: list[float]) -> str:
    """asyncpg has no native pgvector codec; the text literal is the contract.

    中文：asyncpg 没有内置 pgvector codec，因此使用文本字面量传值。

    Args:
        vector: Embedding vector to render.

    Returns:
        The vector formatted as a pgvector text literal.
    """
    return "[" + ",".join(f"{component:.8f}" for component in vector) + "]"
