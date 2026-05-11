from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from second_brain.config import load_config
from second_brain.embedding_providers import HashEmbeddingProvider, build_embedding_provider
from second_brain.extraction.providers import (
    CLICommandLLMProvider,
    OpenAICompatibleLLMProvider,
    build_llm_provider,
    build_vlm_provider,
)


def _write_config(workspace: Path, body: str) -> Path:
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    path = config_dir / "memory.toml"
    path.write_text(
        f"""
[paths]
vault_root = "brain"
db_path = "brain/data/archive.db"
vectors_dir = "brain/data/vectors"
graph_dir = "brain/data/graph"
eval_queries = "evals/queries.jsonl"

{body}
""",
        encoding="utf-8",
    )
    return path


class InferenceProviderConfigTest(unittest.TestCase):
    def test_hash_embedding_provider_remains_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = load_config(_write_config(workspace, ""), workspace)
            provider = build_embedding_provider(config)
            self.assertIsInstance(provider, HashEmbeddingProvider)
            self.assertEqual(provider.model_name, "hash-256-v1")
            self.assertEqual(len(provider.embed_texts(["hello"])[0]), 256)

    def test_models_llm_overrides_legacy_extraction_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = load_config(
                _write_config(
                    workspace,
                    """
[extraction]
llm_provider = "fixture"
llm_model = "fixture-llm"

[models.llm]
provider = "openai_compatible"
model = "local-json-model"
base_url = "http://127.0.0.1:8000/v1"
api_key_env = "LOCAL_KEY"
timeout_seconds = 12
""",
                ),
                workspace,
            )
            provider = build_llm_provider(config)
            self.assertIsInstance(provider, OpenAICompatibleLLMProvider)
            self.assertEqual(provider.model_name, "local-json-model")
            self.assertEqual(provider.base_url, "http://127.0.0.1:8000/v1")
            self.assertEqual(provider.api_key_env, "LOCAL_KEY")
            self.assertEqual(provider.timeout_seconds, 12)

    def test_cli_command_llm_provider_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = load_config(
                _write_config(
                    workspace,
                    """
[models.llm]
provider = "cli_command"
model = "claude-code"
command = "claude -p"
timeout_seconds = 9
""",
                ),
                workspace,
            )
            provider = build_llm_provider(config)
            self.assertIsInstance(provider, CLICommandLLMProvider)
            self.assertEqual(provider.command, "claude -p")
            self.assertEqual(provider.timeout_seconds, 9)

    def test_openai_compatible_vlm_uses_provider_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = load_config(
                _write_config(
                    workspace,
                    """
[models.vlm]
provider = "openai_compatible"
model = "vision-model"
base_url = "http://127.0.0.1:8000/v1"
api_key_env = "VISION_KEY"
""",
                ),
                workspace,
            )
            provider = build_vlm_provider(config)
            self.assertEqual(provider.provider_name, "openai_compatible")
            self.assertEqual(provider.model_name, "vision-model")


if __name__ == "__main__":
    unittest.main()
