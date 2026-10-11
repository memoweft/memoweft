"""Pinned publisher E5 qint8; no training framework, remote code or service."""
from __future__ import annotations

from hashlib import sha256
import importlib
from pathlib import Path
import threading
from typing import Any, Sequence
import uuid

import httpx

MODEL_ID = "intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3:qint8:mean:l2:512"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
DIMENSIONS = 384
FILES = {
    "model_qint8_avx512_vnni.onnx": (118346824, "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"),
    "tokenizer.json": (17082730, "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
}


def file_valid(path: Path, size: int, digest: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest() == digest


def prepare_files(folder: Path, stop: threading.Event) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(10, read=15)) as client:
        for name, (size, digest) in FILES.items():
            destination = folder / name
            if file_valid(destination, size, digest):
                continue
            partial = folder / (name + "." + uuid.uuid4().hex + ".partial")
            try:
                url = f"https://huggingface.co/intfloat/multilingual-e5-small/resolve/{REVISION}/onnx/{name}"
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    count = 0
                    with partial.open("wb") as handle:
                        for chunk in response.iter_bytes(1024 * 1024):
                            if stop.is_set():
                                raise InterruptedError("semantic shutdown")
                            count += len(chunk)
                            if count > size:
                                raise ValueError("model file size mismatch")
                            handle.write(chunk)
                if not file_valid(partial, size, digest):
                    raise ValueError("model file hash mismatch")
                partial.replace(destination)
            finally:
                partial.unlink(missing_ok=True)


class E5Encoder:
    def __init__(self, folder: Path, stop: threading.Event) -> None:
        # Check dependencies before attempting any network access.
        self.np: Any = importlib.import_module("numpy")
        ort: Any = importlib.import_module("onnxruntime")
        tokenizers: Any = importlib.import_module("tokenizers")
        prepare_files(folder, stop)
        if stop.is_set():
            raise InterruptedError("semantic shutdown")
        self.tokenizer = tokenizers.Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(folder / "model_qint8_avx512_vnni.onnx"),
            sess_options=options, providers=["CPUExecutionProvider"])
        self._lock = threading.Lock()

    def encode(self, texts: Sequence[str], *, query: bool = False) -> Any:
        with self._lock:
            encoded = self.tokenizer.encode_batch([("query: " if query else "passage: ") + text for text in texts])
            np = self.np
            ids = np.array([item.ids for item in encoded], dtype=np.int64)
            mask = np.array([item.attention_mask for item in encoded], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in {item.name for item in self.session.get_inputs()}:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]
            if hidden.ndim != 3:
                raise ValueError("unsupported E5 pooling contract")
            vectors = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            if vectors.shape[1] != DIMENSIONS or not np.isfinite(vectors).all():
                raise ValueError("invalid E5 vector")
            return vectors.astype("<f4")
