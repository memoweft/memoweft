# Synthetic bridge memory measurement

Run from the repository root with the Python environment used for Core tests:

```powershell
py/.venv/Scripts/python.exe bench/bridge-memory/measure.py .local/evidence/bridge-memory.jsonl --world-fixtures
```

This starts the real `python -m memoweft.integrations.dsh_bridge` with a new,
automatically removed synthetic data home. It ingests 32, 128 and 512 independent
conversations/sources, optionally inserts matching synthetic World fixtures, and
measures source-list reads, interaction queries and recall batches separately.
It then holds the corpus fixed and performs 32, 128 and 256 additional source-list
reads. `--core-source` can select an isolated baseline source checkout. Every
output filename must be new. Do not point any test at an existing personal home.

The optional `sitecustomize` observer only wraps the real handler and samples
after marked requests. It preserves the original return/exception in `finally`
and swallows observation failures. Output contains request counts, stage enums,
Windows private commit/current/peak working set, current/peak `tracemalloc` heap,
and allocation filenames/line numbers/counts. It contains no source text,
credentials, home path or account identifiers. Tracing itself adds memory and
runtime overhead; compare runs with the same observer. These counters are bytes.
Logs and generated output belong in ignored `.local/evidence/`.

The experiment deliberately disables semantic loading and model routing. Its
model-call and embedding counts are zero; it isolates response retention from
model inference, vector matrices and background formation. World records in
`--world-fixtures` mode are fixtures, not claimed model-formation acceptance.
SQLite uses the application's unchanged default cache settings. A rise with a
fixed corpus and increasing requests cannot be explained by corpus size alone.

The automated regression in `py/tests/test_dsh_replay_memory.py` checks the
retained Python heap, independent of Windows allocator/OS residency: after 80
warm reads, another 240 reads must add less than 2 MiB and the measured heap must
remain below 12 MiB (8 MiB replay budget plus 4 MiB query/module allowance).
Separate tests exercise small replies, byte/count eviction, detached replays,
conflict checks and durable boundary replay after eviction.
