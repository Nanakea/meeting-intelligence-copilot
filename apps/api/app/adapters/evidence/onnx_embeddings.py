"""Optional, hash-pinned local ONNX embedding provider."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from app.domain.evidence import EmbeddingProfile


class OnnxMultilingualE5Provider:
    """Load a user-installed model bundle without network access.

    The bundle must contain ``model.onnx`` and ``tokenizer.json``. Imports are
    intentionally lazy so the normal backend has no ONNX/tokenizers dependency.
    """

    def __init__(
        self,
        model_directory: Path,
        *,
        expected_bundle_sha256: str,
        dimensions: int = 384,
    ) -> None:
        self._model_path = model_directory / "model.onnx"
        self._tokenizer_path = model_directory / "tokenizer.json"
        digest = hashlib.sha256()
        for path in (self._model_path, self._tokenizer_path):
            if not path.is_file():
                raise OSError("local embedding model bundle is incomplete")
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        actual = digest.hexdigest()
        if actual != expected_bundle_sha256:
            raise ValueError("local embedding model bundle hash mismatch")
        self._profile = EmbeddingProfile(
            model_id="intfloat/multilingual-e5-small",
            model_sha256=actual,
            dimensions=dimensions,
        )
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise OSError("install the rag-local dependency group") from exc
        self._tokenizer = Tokenizer.from_file(str(self._tokenizer_path))
        self._session = ort.InferenceSession(
            str(self._model_path), providers=["CPUExecutionProvider"]
        )

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_sync, texts, "passage")

    async def embed_query(self, text: str) -> list[float]:
        return (await asyncio.to_thread(self._embed_sync, [text], "query"))[0]

    def _embed_sync(self, texts: list[str], prefix: str) -> list[list[float]]:
        import numpy as np

        encoded = self._tokenizer.encode_batch([f"{prefix}: {text}" for text in texts])
        maximum = min(512, max((len(value.ids) for value in encoded), default=1))
        input_ids = []
        attention_mask = []
        for value in encoded:
            ids = value.ids[:maximum]
            mask = value.attention_mask[:maximum]
            padding = maximum - len(ids)
            input_ids.append(ids + [0] * padding)
            attention_mask.append(mask + [0] * padding)
        outputs = self._session.run(
            None,
            {
                "input_ids": np.asarray(input_ids, dtype=np.int64),
                "attention_mask": np.asarray(attention_mask, dtype=np.int64),
            },
        )[0]
        mask = np.asarray(attention_mask, dtype=np.float32)[..., None]
        pooled = (outputs * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        return pooled.tolist()
