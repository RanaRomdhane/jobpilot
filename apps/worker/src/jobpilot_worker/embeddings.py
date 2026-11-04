"""Local multilingual embeddings: intfloat/multilingual-e5-small, ONNX int8, on the CPU.

No API cost and no text leaves the machine. The model files are downloaded once from Hugging Face
at a pinned revision and verified against their SHA-256 before use (supply chain), then cached in
`<storage_dir>/models`. e5 expects "query: " / "passage: " prefixes; vectors are mean-pooled and
L2-normalized, so the cosine distance in pgvector is 1 - dot product.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import numpy as np

# onnxruntime ships Microsoft telemetry (with HTTPS upload) that starts at import time: must be
# switched off before the import (also set in infra/docker-compose.yml)
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

log = logging.getLogger(__name__)

REPO = "Xenova/multilingual-e5-small"
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
# path in the repo -> sha256 of the public file (checksums, not secrets: "token" in
# "tokenizer.json" trips gitleaks' generic-api-key rule, hence the inline allow)
FILES: dict[str, str] = {
    "onnx/model_quantized.onnx": "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    "tokenizer.json": (
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"  # gitleaks:allow
    ),
}
DIM = 384
MAX_TOKENS = 512
BATCH = 16

Kind = Literal["query", "passage"]


class EmbeddingUnavailableError(RuntimeError):
    pass


class _Session(Protocol):
    def run(self, output_names: Any, input_feed: dict[str, Any]) -> list[Any]: ...

    def get_inputs(self) -> list[Any]: ...


class _Tokenizer(Protocol):
    def encode_batch(self, texts: list[str]) -> list[Any]: ...


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def ensure_model(model_dir: Path, *, user_agent: str) -> Path:
    """Download the pinned files if missing; refuse any file whose hash does not match."""
    target = model_dir / REPO.replace("/", "--") / REVISION
    target.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=300, headers={"User-Agent": user_agent}
    ) as http:
        for name, digest in FILES.items():
            path = target / name
            if path.exists() and await asyncio.to_thread(_sha256, path) == digest:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
            log.info("downloading embedding model file %s", name)
            async with http.stream("GET", url) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in resp.aiter_bytes(1 << 20):
                        f.write(chunk)
            if await asyncio.to_thread(_sha256, tmp) != digest:
                tmp.unlink(missing_ok=True)
                raise EmbeddingUnavailableError(f"{name}: checksum mismatch, refusing the file")
            tmp.replace(path)
    return target


class Embedder:
    def __init__(self, session: _Session, tokenizer: _Tokenizer) -> None:
        self._session = session
        self._tok = tokenizer
        self._inputs = {i.name for i in session.get_inputs()}

    @classmethod
    async def load(cls, model_dir: Path, *, user_agent: str, threads: int = 2) -> Embedder:
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - installed with the worker
            raise EmbeddingUnavailableError(str(exc)) from exc
        ort.disable_telemetry_events()  # belt and braces, on top of ORT_DISABLE_TELEMETRY
        path = await ensure_model(model_dir, user_agent=user_agent)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(path / "onnx/model_quantized.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        tok.enable_truncation(MAX_TOKENS)
        tok.enable_padding()
        return cls(session, tok)

    def _batch(self, texts: list[str]) -> np.ndarray:
        enc = self._tok.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed: dict[str, Any] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = np.asarray(self._session.run(None, feed)[0], dtype=np.float32)  # [b, seq, dim]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        out: np.ndarray = (pooled / np.clip(norms, 1e-12, None)).astype(np.float32)
        return out

    def embed(self, texts: list[str], kind: Kind) -> np.ndarray:
        """Synchronous (CPU-bound): call through `asyncio.to_thread`."""
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        prefixed = [f"{kind}: {' '.join(t.split())}" for t in texts]
        out: np.ndarray = np.vstack(
            [self._batch(prefixed[i : i + BATCH]) for i in range(0, len(prefixed), BATCH)]
        )
        return out

    async def aembed(self, texts: list[str], kind: Kind) -> np.ndarray:
        return await asyncio.to_thread(self.embed, texts, kind)
