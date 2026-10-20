"""Lazy background indexing and per-account in-memory cosine matrices."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import importlib
import importlib.util
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Protocol, Sequence

from .config import SemanticConfig
from .model import DIMENSIONS, E5Encoder, MODEL_ID
from . import store


@dataclass(frozen=True)
class SemanticCandidate:
    kind: str
    item_id: str
    score: float
    content_hash: str


class CandidateProvider(Protocol):
    config: SemanticConfig

    def candidates(self, db: sqlite3.Connection, subject_id: str, query: str) -> Sequence[SemanticCandidate]: ...


class Encoder(Protocol):
    def encode(self, texts: Sequence[str], *, query: bool = False) -> Any: ...


class SemanticRuntime:
    def __init__(self, path: Path, config: SemanticConfig,
                 encoder_factory: Callable[[Path, threading.Event], Encoder] = E5Encoder) -> None:
        self.path, self.config = path.resolve(), config
        self._factory = encoder_factory
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._inference = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        self._encoder: Encoder | None = None
        self._state = "preparing" if config.enabled else "disabled"
        self._reason: str | None = "not_loaded" if config.enabled else None
        self._matrices: dict[str, tuple[int, list[tuple[str, str, str]], Any]] = {}
        self.indexed_count = 0
        self.backfill_complete = False

    def status(self) -> dict[str, object]:
        with self._lock:
            return {"state": self._state, "reason": self._reason, "enabled": self.config.enabled,
                    "model_id": MODEL_ID, "threshold": self.config.threshold,
                    "max_additions": self.config.max_additions, "indexed_count": self.indexed_count,
                    "backfill_complete": self.backfill_complete}

    def start(self) -> bool:
        """Return immediately; this call's recall must use the lexical path."""
        with self._lock:
            if not self.config.enabled or self._stop.is_set() or self._thread is not None:
                return False
            self._state, self._reason = "preparing", None
            self._thread = threading.Thread(target=self._run, name="memoweft-semantic", daemon=True)
            self._thread.start()
            return True

    def close(self) -> None:
        self._stop.set()
        # No network/inference wait on the host's shutdown critical path.
        thread = self._thread
        if thread is not None:
            thread.join(timeout=0.2)
        if self._cleanup_thread is not None:
            self._cleanup_thread.join(timeout=0.2)
        with self._lock:
            self._matrices.clear()
            self._encoder = None
            self._state, self._reason = "unavailable", "stopped"

    def start_cleanup(self) -> None:
        """Expiry runs even while downloading, offline, or switched off."""
        with self._lock:
            if self._cleanup_thread is None and not self._stop.is_set():
                self._cleanup_thread = threading.Thread(target=self._cleanup, name="memoweft-semantic-expiry", daemon=True)
                self._cleanup_thread.start()

    def _cleanup(self) -> None:
        with closing(sqlite3.connect(str(self.path), isolation_level=None, timeout=0.1)) as db:
            db.execute("PRAGMA secure_delete=ON")
            while not self._stop.is_set():
                try:
                    if store.expire(db):
                        with self._lock:
                            self._matrices.clear()
                except sqlite3.Error:
                    pass
                self._stop.wait(0.1)

    def _run(self) -> None:
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(str(self.path), isolation_level=None, timeout=0.1)
            db.execute("PRAGMA secure_delete=ON")
            while not self._stop.is_set():
                try:
                    db.execute("BEGIN IMMEDIATE")
                    store.install(db)
                    store.expire(db)
                    db.commit()
                    break
                except sqlite3.OperationalError:
                    db.rollback()
                    self._stop.wait(0.1)
            if self._stop.is_set():
                return
            self.start_cleanup()
            folder = self.config.cache_dir or self.path.parent / "cache" / "multilingual-e5-small"
            encoder = self._factory(folder, self._stop)
            if self._stop.is_set():
                return
            with self._lock:
                self._encoder = encoder
                self._state, self._reason = "ready", None
            cursors = {kind: "" for kind in store.TABLES}
            done: set[str] = set()
            last_epoch = -1
            while not self._stop.is_set():
                try:
                    store.expire(db)
                    epoch = int(db.execute("SELECT value FROM semantic_epoch").fetchone()[0])
                    if epoch != last_epoch:
                        with self._lock:
                            self._matrices.clear()
                        last_epoch = epoch
                    db.execute("BEGIN IMMEDIATE")
                    next_cursors, next_done = dict(cursors), set(done)
                    for kind in store.TABLES:
                        if kind not in done:
                            next_cursors[kind], finished = store.enqueue_backfill(db, kind, cursors[kind], self.config.batch_size)
                            if finished:
                                next_done.add(kind)
                    db.commit()
                    cursors, done = next_cursors, next_done
                    self.process_batch(db, encoder)
                    self.indexed_count = int(db.execute("SELECT count(*) FROM semantic_vector WHERE model_id=?", (MODEL_ID,)).fetchone()[0])
                    pending = db.execute("SELECT 1 FROM semantic_pending LIMIT 1").fetchone()
                    self.backfill_complete = len(done) == len(store.TABLES) and pending is None
                    self._stop.wait(0.1 if self.backfill_complete else 0.005)
                except sqlite3.OperationalError:
                    if db.in_transaction:
                        db.rollback()
                    self._stop.wait(0.1)
        except Exception as exc:
            with self._lock:
                self._state = "unavailable"
                # Never expose paths, URLs, user content or request details.
                self._reason = "dependency_missing" if isinstance(exc, ModuleNotFoundError) else type(exc).__name__
                self._encoder = None
                self._matrices.clear()
        finally:
            if db is not None:
                db.close()

    def process_batch(self, db: sqlite3.Connection, encoder: Encoder) -> int:
        pending = db.execute("SELECT kind,item_id FROM semantic_pending ORDER BY kind,item_id LIMIT ?", (self.config.batch_size,)).fetchall()
        records = [(str(kind), str(item_id), store.eligible_record(db, str(kind), str(item_id))) for kind, item_id in pending]
        eligible = [(kind, item_id, record) for kind, item_id, record in records if record is not None]
        vectors: Any = []
        if eligible:
            with self._inference:
                vectors = encoder.encode([record[1] for _, _, record in eligible])
        if self._stop.is_set():
            return 0
        db.execute("BEGIN IMMEDIATE")
        written = 0
        try:
            # Clear only the version we encoded. A concurrently changed item
            # remains queued; forgetting between encode and commit cannot revive it.
            for kind, item_id, record in records:
                current = store.eligible_record(db, kind, item_id)
                if current == record:
                    db.execute("DELETE FROM semantic_pending WHERE kind=? AND item_id=?", (kind, item_id))
            for (kind, item_id, record), vector in zip(eligible, vectors):
                written += store.publish(db, kind, item_id, store.content_hash(record[1]), vector.astype("<f4").tobytes())
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return written

    def score_texts(self, query: str, texts: Sequence[str]) -> Sequence[float]:
        """Ephemeral source ranking; no source text/vector is retained or written.

        Callers must authorize sources in their read snapshot before calling.
        A busy/unavailable encoder leaves the existing formal recall intact.
        """
        if len(query) > 4000 or not texts or not self.config.enabled or self._stop.is_set():
            return ()
        if not self._inference.acquire(blocking=False):
            return ()
        try:
            with self._lock:
                encoder = self._encoder if self._state == 'ready' else None
            if encoder is None:
                return ()
            vector = encoder.encode([query], query=True)[0]
            scores: list[float] = []
            for start in range(0, len(texts), self.config.batch_size):
                scores.extend(float(value) for value in encoder.encode(texts[start:start + self.config.batch_size]) @ vector)
            return scores
        finally:
            self._inference.release()

    def candidates(self, db: sqlite3.Connection, subject_id: str, query: str) -> Sequence[SemanticCandidate]:
        if len(query) > 4000:
            return ()
        if self.start():
            return ()
        if not self.config.enabled or self._stop.is_set() or not self._inference.acquire(blocking=False):
            return ()
        try:
            with self._lock:
                encoder = self._encoder
                if self._state != "ready" or encoder is None:
                    return ()
                epoch = int(db.execute("SELECT value FROM semantic_epoch").fetchone()[0])
                cached = self._matrices.get(subject_id)
                if cached is None or cached[0] != epoch:
                    np: Any = importlib.import_module("numpy")
                    rows = db.execute("SELECT kind,item_id,content_hash,vector FROM semantic_vector WHERE subject_id=? AND model_id=? AND dimensions=? ORDER BY kind,item_id", (subject_id, MODEL_ID, DIMENSIONS)).fetchall()
                    keys, vectors = [], []
                    for kind, item_id, digest, blob in rows:
                        if len(blob) != DIMENSIONS * 4:
                            continue
                        vector = np.frombuffer(blob, dtype="<f4")
                        if not np.isfinite(vector).all() or abs(float(np.linalg.norm(vector)) - 1) > 0.01:
                            continue
                        keys.append((str(kind), str(item_id), str(digest)))
                        vectors.append(vector)
                    matrix = np.stack(vectors) if vectors else np.empty((0, DIMENSIONS), dtype="<f4")
                    cached = epoch, keys, matrix
                    self._matrices[subject_id] = cached
            _, keys, matrix = cached
            if not keys:
                return ()
            scores = matrix @ encoder.encode([query], query=True)[0]
            # Bounded nomination pool. Final authorisation and the 3-item cap
            # belong to recall, after every authoritative check.
            np = importlib.import_module("numpy")
            matching = np.flatnonzero(scores >= self.config.threshold)
            # Partition before the stable tie-break to avoid sorting 50k rows.
            if len(matching) > 32:
                cutoff = float(np.partition(scores[matching], -32)[-32])
                matching = matching[scores[matching] >= cutoff]
            ranked = sorted((int(i) for i in matching),
                            key=lambda i: (-float(scores[i]), keys[i][1], keys[i][0]))[:32]
            return tuple(SemanticCandidate(keys[i][0], keys[i][1], float(scores[i]), keys[i][2]) for i in ranked)
        except Exception as exc:
            with self._lock:
                self._state, self._reason = "unavailable", type(exc).__name__
                self._matrices.clear()
            return ()
        finally:
            self._inference.release()


_RUNTIMES: dict[Path, SemanticRuntime] = {}
_REGISTRY_LOCK = threading.Lock()


def runtime_for(path: Path, config: SemanticConfig | None = None) -> SemanticRuntime:
    path = path.resolve()
    with _REGISTRY_LOCK:
        if path not in _RUNTIMES:
            try:
                chosen = config or SemanticConfig.from_env()
                _RUNTIMES[path] = SemanticRuntime(path, chosen)
            except ValueError:
                _RUNTIMES[path] = SemanticRuntime(path, SemanticConfig(enabled=False))
                _RUNTIMES[path]._state, _RUNTIMES[path]._reason = "unavailable", "invalid_configuration"
        return _RUNTIMES[path]


def provider_for(db: sqlite3.Connection) -> CandidateProvider | None:
    # Read-only introspection; never mutate or migrate on the recall connection.
    path = next((str(row[2]) for row in db.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not path:
        return None
    with _REGISTRY_LOCK:
        existing = _RUNTIMES.get(Path(path).resolve())
    if existing is not None:
        return existing
    if os.environ.get("MEMOWEFT_SEMANTIC_ENABLED", "1").lower() in {"0", "false", "off"}:
        return None
    if any(importlib.util.find_spec(name) is None for name in ("numpy", "onnxruntime", "tokenizers")):
        return None
    try:
        return runtime_for(Path(path))
    except (ValueError, OSError):
        return None


def close_runtime(path: Path) -> None:
    with _REGISTRY_LOCK:
        runtime = _RUNTIMES.pop(path.resolve(), None)
    if runtime is not None:
        runtime.close()


def configure_runtime(path: Path, config: SemanticConfig | None = None) -> SemanticRuntime:
    """Integration startup: install only the cheap derived schema, no model I/O."""
    runtime = runtime_for(path, config)
    if runtime.config.enabled:
        try:
            with closing(sqlite3.connect(str(path), timeout=0.1)) as db:
                db.execute("BEGIN IMMEDIATE")
                store.install(db)
                store.expire(db)
                db.commit()
        except sqlite3.Error:
            # The lazy worker retries setup without blocking normal host startup.
            pass
    try:
        with closing(sqlite3.connect(str(path), timeout=0.1)) as db:
            installed = db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_vector'").fetchone()
        if installed:
            runtime.start_cleanup()
    except sqlite3.Error:
        pass
    return runtime
