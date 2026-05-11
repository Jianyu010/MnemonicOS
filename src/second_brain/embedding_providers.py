from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Protocol
from urllib import request
from urllib.parse import urlparse

from .config import AppConfig
from .semantics import encode_text


@dataclass(slots=True)
class ProviderHealth:
    ok: bool
    status: str
    detail: str = ""


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...

    def healthcheck(self) -> ProviderHealth:
        ...


class HashEmbeddingProvider:
    provider_name = "hash"

    def __init__(self, model_name: str, *, dimensions: int) -> None:
        self.model_name = model_name
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [encode_text(text, dimensions=self.dimensions) for text in texts]

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(ok=True, status="available", detail="zero-dependency hash encoder")


class OllamaEmbeddingProvider:
    provider_name = "ollama_embeddings"

    def __init__(
        self,
        model_name: str,
        *,
        dimensions: int,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: int = 60,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            payload = {"model": self.model_name, "prompt": text}
            data = _json_request(
                f"{self.base_url}/api/embeddings",
                payload,
                timeout_seconds=self.timeout_seconds,
            )
            vector = data.get("embedding")
            if not isinstance(vector, list):
                raise RuntimeError("ollama embedding response missing embedding")
            vectors.append([float(value) for value in vector])
        return vectors

    def healthcheck(self) -> ProviderHealth:
        try:
            self.embed_texts(["healthcheck"])
        except Exception as exc:
            return ProviderHealth(ok=False, status="provider_unavailable", detail=str(exc))
        return ProviderHealth(ok=True, status="available")


class OpenAICompatibleEmbeddingProvider:
    provider_name = "openai_compatible_embeddings"

    def __init__(
        self,
        model_name: str,
        *,
        dimensions: int,
        base_url: str,
        api_key_env: str = "",
        timeout_seconds: int = 60,
    ) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.model_name, "input": texts}
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        headers = _auth_headers(self.api_key_env)
        data = _json_request(
            f"{self.base_url}/embeddings",
            payload,
            headers=headers,
            timeout_seconds=self.timeout_seconds,
        )
        rows = data.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("embedding response missing data")
        rows = sorted(rows, key=lambda row: int(row.get("index", 0)) if isinstance(row, dict) else 0)
        vectors: list[list[float]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("embedding"), list):
                raise RuntimeError("embedding response row missing embedding")
            vectors.append([float(value) for value in row["embedding"]])
        return vectors

    def healthcheck(self) -> ProviderHealth:
        try:
            self.embed_texts(["healthcheck"])
        except Exception as exc:
            return ProviderHealth(ok=False, status="provider_unavailable", detail=str(exc))
        return ProviderHealth(ok=True, status="available")


def build_embedding_provider(config: AppConfig) -> EmbeddingProvider:
    provider = config.embeddings.provider.strip().casefold()
    if provider in {"", "hash"}:
        return HashEmbeddingProvider(config.embeddings.model, dimensions=config.embeddings.dimensions)
    if provider in {"ollama", "ollama_embeddings"}:
        base_url = config.embeddings.base_url or config.extraction.ollama_base_url
        return OllamaEmbeddingProvider(
            config.embeddings.model,
            dimensions=config.embeddings.dimensions,
            base_url=base_url,
            timeout_seconds=config.embeddings.timeout_seconds,
        )
    if provider in {"openai", "openai_compatible", "openai_compatible_embeddings"}:
        if not config.embeddings.base_url:
            raise ValueError("embeddings.base_url is required for openai_compatible_embeddings")
        return OpenAICompatibleEmbeddingProvider(
            config.embeddings.model,
            dimensions=config.embeddings.dimensions,
            base_url=config.embeddings.base_url,
            api_key_env=config.embeddings.api_key_env,
            timeout_seconds=config.embeddings.timeout_seconds,
        )
    raise ValueError(f"unsupported embedding provider: {config.embeddings.provider}")


def embed_one(config: AppConfig, text: str) -> list[float]:
    return build_embedding_provider(config).embed_texts([text])[0]


def embedding_model_key(config: AppConfig) -> str:
    return config.embeddings.model


def _auth_headers(api_key_env: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        token = os.environ.get(api_key_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _json_request(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: int,
) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers or {"Content-Type": "application/json"},
        method="POST",
    )
    with _open_request(req, timeout_seconds=timeout_seconds) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("provider response was not a JSON object")
    return data


def _open_request(req: request.Request, *, timeout_seconds: int):
    host = urlparse(req.full_url).hostname or ""
    if host in {"127.0.0.1", "localhost", "::1"}:
        opener = request.build_opener(request.ProxyHandler({}))
        return opener.open(req, timeout=timeout_seconds)
    return request.urlopen(req, timeout=timeout_seconds)
