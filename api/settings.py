"""Configuration. Every value is read from the environment or a .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

EmbeddingProvider = Literal["openai", "gemini", "hashing"]


class Settings(BaseSettings):
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
            "'openai' or 'gemini' call a hosted embedding API. 'hashing' is a "
            "deterministic local fallback that needs no key and no network; it "
            "captures lexical overlap only, NOT semantics. Never report a "
            "benchmark produced under 'hashing' as a semantic result."
        ),
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
        default=0.92,
        ge=0.0,
        le=1.0,
        description=(
            "Cosine similarity at or above which an incoming preference is "
            "treated as the same fact and refreshed instead of inserted."
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

    def masked(self) -> dict[str, object]:
        """Settings safe to log or expose on /healthz."""
        return {
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "session_buffer_window": self.session_buffer_window,
            "decay_rate_per_day": self.decay_rate_per_day,
            "dedup_threshold": self.dedup_threshold,
            "cache_enabled": self.cache_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
