"""CPU-only E5, explicit file downloads; no remote code or service endpoints."""
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

import httpx
import numpy as np
import onnxruntime as ort
import psutil
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[3]
MODEL_ID = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
FILES = ["onnx/model_qint8_avx512_vnni.onnx", "onnx/tokenizer.json", "onnx/config.json", "README.md"]
MODEL_DIR = ROOT / ".local/mem5/model"


def download():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=180) as client:
        for file in FILES:
            path = MODEL_DIR / file.rsplit("/", 1)[-1]
            if path.exists():
                continue
            with client.stream("GET", f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{file}") as response:
                response.raise_for_status()
                partial = path.with_suffix(path.suffix + ".partial")
                with partial.open("wb") as out:
                    for chunk in response.iter_bytes():
                        out.write(chunk)
                partial.replace(path)


class Embedder:
    def __init__(self):
        before = psutil.Process().memory_info().rss
        started = perf_counter()
        self.tokenizer = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(MODEL_DIR / "model_qint8_avx512_vnni.onnx"), sess_options=options, providers=["CPUExecutionProvider"])
        self.metadata = {
            "model": MODEL_ID, "revision": REVISION, "license": "MIT", "dimensions": 384,
            "model_source": f"https://huggingface.co/{MODEL_ID}/tree/{REVISION}/onnx",
            "license_source": f"https://huggingface.co/{MODEL_ID}/blob/{REVISION}/README.md",
            "files": {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p.read_bytes()).hexdigest()} for p in MODEL_DIR.iterdir() if p.name in {f.rsplit('/', 1)[-1] for f in FILES}},
            "load_ms": (perf_counter() - started) * 1000,
            "rss_after_load_bytes": psutil.Process().memory_info().rss,
            "rss_load_delta_bytes": psutil.Process().memory_info().rss - before,
            "providers": self.session.get_providers(), "intra_op_threads": 2,
            "max_tokens": 512, "pooling": "attention-mask mean; L2 normalized",
            "quantization": "Publisher qint8 ONNX; CPUExecutionProvider only",
        }

    def encode(self, texts, query=False):
        encoded = self.tokenizer.encode_batch([("query: " if query else "passage: ") + text for text in texts])
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        names = {i.name for i in self.session.get_inputs()}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.zeros_like(ids)
        outputs = self.session.run(None, feed)
        hidden = outputs[0]
        if hidden.ndim != 3:
            raise ValueError("Expected token embeddings, not an unknown pooling contract")
        embeddings = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        return (embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)).astype(np.float32)


if __name__ == "__main__":
    download()
    print(json.dumps({"downloaded": [p.name for p in MODEL_DIR.iterdir()]}, indent=2))
