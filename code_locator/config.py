"""Configuration loading for Code Locator."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CodeLocatorConfig:
    """Code Locator configuration."""

    # Storage
    sqlite_db: str = "~/.bicameral/code-graph.db"

    # BM25
    bm25_backend: str = "bm25s"  # "bm25s" for MVP, "zoekt" post-MVP

    # RRF
    rrf_k: int = 60
    max_retrieval_results: int = 20
    channel_weights: dict[str, float] = field(
        default_factory=lambda: {"bm25": 1.0, "graph": 1.2, "vector": 0.6}
    )

    # Vector search (legacy fallback — cocoindex-code daemon, use indexing_backend="cocoindex" instead)
    vector_enabled: bool = False
    vector_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # CocoIndex backend (Option A+) — writes to sqlite_db (single DB)
    indexing_backend: str = "legacy"  # "legacy" | "cocoindex"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunk_size: int = 512
    chunk_overlap: int = 50

    # Graph
    graph_hop_depth: int = 1
    max_neighbors_per_result: int = 10

    # Vocabulary bridge
    fuzzy_threshold: int = 80
    fuzzy_scorer: str = "WRatio"
    fuzzy_max_matches_per_candidate: int = 3
    min_candidate_length: int = 2

    def resolve_paths(self) -> CodeLocatorConfig:
        """Expand ~ in all path fields."""
        self.sqlite_db = str(Path(self.sqlite_db).expanduser())
        return self


def load_config(config_path: str | None = None) -> CodeLocatorConfig:
    """Load config from YAML file, falling back to defaults.

    Config values can be overridden by environment variables prefixed with
    CODE_LOCATOR_ (e.g., CODE_LOCATOR_FUZZY_THRESHOLD=90).
    """
    config_data: dict = {}

    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
            config_data = raw.get("code_locator", raw)

    # Environment variable overrides
    for key in CodeLocatorConfig.__dataclass_fields__:
        env_key = f"CODE_LOCATOR_{key.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            field_type = CodeLocatorConfig.__dataclass_fields__[key].type
            if field_type == "int":
                config_data[key] = int(env_val)
            elif field_type == "float":
                config_data[key] = float(env_val)
            elif field_type == "bool":
                config_data[key] = env_val.lower() in ("true", "1", "yes")
            else:
                config_data[key] = env_val

    return CodeLocatorConfig(**config_data).resolve_paths()
