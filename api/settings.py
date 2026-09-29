"""Configuration defaults, overridable by the environment or a .env file.

中文说明：配置项在代码中提供安全默认值，也可由环境变量或 .env 文件覆盖。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

EmbeddingProvider = Literal["openai", "gemini", "ollama", "hashing"]


class Settings(BaseSettings):
    # All runtime configuration, grouped by concern. This must remain a comment:
    # Pydantic copies class docstrings into JSON schema descriptions.
    # 中文：记忆引擎的全部运行时配置，按基础设施、嵌入、抽取、记忆行为与缓存
    # 分组。这里必须使用普通注释；Pydantic 会把 class docstring 写入 JSON schema。
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MINDBRIDGE_",
        extra="ignore",
    )

    # --- infrastructure ---------------------------------------------------
    database_url: PostgresDsn = Field(
        default="postgresql://mindbridge:mindbridge@localhost:5433/mindbridge",  # type: ignore[arg-type]
        description="Postgres with the pgvector extension available.",
    )
    redis_url: RedisDsn = Field(
        default="redis://localhost:6379/0",  # type: ignore[arg-type]
    )
    db_pool_min: int = 1
    db_pool_max: int = 10

    # --- embeddings -------------------------------------------------------
    embedding_provider: EmbeddingProvider = Field(
        default="hashing",
        description=(
            "'ollama' runs a real embedding model locally (no key, nothing "
            "leaves the machine) and is the only option under which write-time "
            "dedup actually works. 'openai'/'gemini' call a hosted API. "
            "'hashing' is a deterministic fallback that captures lexical "
            "overlap only, NOT semantics: it scores known duplicates 0.13-0.73, "
            "so no dedup threshold separates them from unrelated facts. Never "
            "report a benchmark produced under 'hashing' as a semantic result."
        ),
    )
    ollama_url: str = Field(
        default="http://localhost:11434",
        description="Use http://host.docker.internal:11434 from a container.",
    )
    embedding_dim: int = Field(
        default=1536,
        description=(
            "Must match the provider's output width and the vector(...) column. "
            "Changing it requires recreating the memory_vectors table: "
            "openai text-embedding-3-small = 1536, gemini text-embedding-004 = 768."
        ),
    )
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    embedding_timeout_seconds: float = 30.0

    # --- local agent ------------------------------------------------------
    agent_model: str = Field(
        default="qwen2.5:7b",
        description="Local Ollama model used by the agent tool-selection loop.",
    )
    agent_timeout_seconds: float = 90.0

    # --- local extraction -------------------------------------------------
    mlx_url: str = Field(
        default="http://127.0.0.1:8080/v1",
        description="OpenAI-compatible base URL exposed by mlx_lm.server.",
    )
    mlx_model: str = Field(
        default="mlx-community/Qwen2.5-3B-Instruct-4bit",
        description="Base-model label recorded on locally extracted cards.",
    )
    mlx_timeout_seconds: float = 180.0

    # --- memory behaviour -------------------------------------------------
    session_buffer_window: int = Field(
        default=12,
        description="Turns kept in T1 before the oldest are eligible for T2.",
    )
    decay_rate_per_day: float = Field(
        default=0.01,
        description=(
            "λ in score = cosine * exp(-λ * Δdays). 0.01 keeps ~70% of the "
            "score after a month; 0.05 keeps ~22%."
        ),
    )
    dedup_threshold: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description=(
            "Cosine similarity at or above which an incoming preference is "
            "treated as the same fact and refreshed instead of inserted. "
            "Model-specific: 0.86 under bge-m3 (129 real pairs read), 0.80 "
            "under nomic-embed-text (170 rows). Below it, merges are mostly "
            "topical rather than duplicate. Retune when changing embedding "
            "model. The default stays 0.80 because the default provider is "
            "not bge-m3; .env.example sets 0.86 alongside the model."
        ),
    )
    default_top_k: int = 5
    superseded_penalty: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Score multiplier applied to superseded rows when they are "
        "explicitly included in a query.",
    )

    # --- cache ------------------------------------------------------------
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300
    cache_lru_size: int = Field(
        default=128,
        ge=0,
        description=(
            "Entries kept in the in-process LRU that sits in front of Redis. "
            "Serves the identical query without a network round trip; bounded "
            "so a long-running API process cannot grow without limit."
        ),
    )
    cache_semantic_enabled: bool = Field(
        default=False,
        description=(
            "Serve a cached temporal_query result to a *different* query whose "
            "embedding is near a cached one. OFF by default, and that default "
            "is a measurement rather than caution: see the `cache_semantic` "
            "entry in evals/results.json. Under nomic-embed-text the two "
            "unrelated questions '回复应该写得多详细' and '测试数据应该怎么准备' "
            "score cosine 0.9992, above the 0.9064 of the one true paraphrase "
            "pair in the set — so every threshold in the sweep, up to 0.99, "
            "still produced false hits. Turn this on only after re-running "
            "evals/eval_memory_engine.py under the embedder you actually "
            "deploy and seeing a threshold with zero false hits."
        ),
    )
    cache_semantic_threshold: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description=(
            "Cosine at or above which a cached query is treated as asking the "
            "same thing. Deliberately NOT the 0.80 dedup threshold: that number "
            "was read off stored preference *statements*, which are long. Cache "
            "keys are *queries*, which are short, and nomic-embed-text is far "
            "less discriminative on short text. This is a separate number that "
            "needs its own evidence, and on the evidence there is no good "
            "value: the sweep never reached zero false hits at any threshold "
            "through 0.99. 0.95 is a deliberately conservative placeholder — "
            "err high, as with dedup, because a missed hit costs one vector "
            "search and a false hit answers the wrong question. It is why "
            "cache_semantic_enabled is False rather than a setting to trust."
        ),
    )
    cache_semantic_margin: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description=(
            "Ambiguity guard. If the runner-up cached query is within this "
            "cosine of the best one, the neighbourhood is not discriminative "
            "and the lookup refuses rather than guessing. This is what turns an "
            "embedder collapse (several unrelated queries all at ~1.0) into a "
            "cache miss instead of another question's memories."
        ),
    )
    cache_semantic_index_size: int = Field(
        default=256,
        ge=1,
        description="Most recent cached queries eligible for semantic matching.",
    )

    def masked(self) -> dict[str, object]:
        """Settings safe to log or expose on /healthz.

        中文：仅返回可安全记录或通过 /healthz 暴露的配置，不包含密钥。

        Returns:
            A dictionary of non-sensitive setting values.
        """
        return {
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "session_buffer_window": self.session_buffer_window,
            "decay_rate_per_day": self.decay_rate_per_day,
            "dedup_threshold": self.dedup_threshold,
            "cache_enabled": self.cache_enabled,
            "cache_semantic_enabled": self.cache_semantic_enabled,
            "cache_semantic_threshold": self.cache_semantic_threshold,
        }


@lru_cache
def get_settings() -> Settings:
    """Build and cache the process-wide Settings instance.

    中文：构建并缓存进程级唯一的 Settings 实例。

    Returns:
        The Settings object reused for the lifetime of the process.
    """
    return Settings()
