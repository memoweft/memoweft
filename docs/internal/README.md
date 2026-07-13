# Internal Design Notes

> **`internal/` vs `internals/`** (one letter apart, easy to confuse): **this** directory (`internal/`) holds **maintainer-only ledgers** — calibration facts, runbooks, feasibility, positioning discipline. The public **"how MemoWeft is built"** mechanism docs (architecture, boundaries, performance, the numbering map) live under [`docs/internals/`](../internals/).

This directory contains a small set of evergreen maintainer notes. It is not required for using MemoWeft or contributing routine changes. The "how it is built" notes (architecture, boundaries, performance) now live under [`docs/internals/`](../internals/).

Public documentation starts at [`docs/README.md`](../README.md). Historical task plans, implementation logs, and status snapshots are intentionally not retained here.
