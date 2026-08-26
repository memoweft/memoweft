# MemoWeft

MemoWeft is a local-first memory engine for AI applications. It keeps Evidence, derived World objects, provenance, revisions, corrections, retractions, permission state, and portable data as distinct records so a host can explain what it remembers and why.

[简体中文](./README.zh-CN.md)

## Package surfaces

The root npm package is the TypeScript library. The `py/` project is the Python MemoWeft 2.0 implementation and contains the production Hermes provider, Trust services, Portable v4, and DSH RPC v2. The source tree also contains a Memory Experience app; that app is not embedded in the root npm tarball.

The Python distribution exposes the `memoweft` provider in the `hermes_agent.memory_providers` entry-point group. Package metadata, source manifests, and installed behavior are separate evidence layers and should be verified independently.

## Install the TypeScript package

Use `npm install memoweft` for a published npm release. Node 24 has a built-in SQLite path; Node 20 and Node 22 use the optional `better-sqlite3` driver.

The repository candidate may be newer than the latest published package. A successful local build or tarball hash does not prove registry publication.

## Memory contracts

MemoWeft keeps these boundaries explicit:

- Raw user content remains exact Evidence with provenance and permissions.
- Entity, Relationship, Event, Cognition, and Evaluation records are formal World objects.
- Recall is deterministic for the same revision and performs no World writes.
- Correction, retract, forget, archive, mute, and permission changes produce durable receipts.
- Portable bundles are versioned, validated, planned before apply, and conflict checked.
- Hermes host events and user presentation remain distinct from Core business terminals.

## Trust boundary

Local-first does not mean that data can never leave the machine. Hosts choose model routes, storage paths, consent flows, authentication, authorization, encryption, backup, and logging policies. MemoWeft permission fields constrain its formal paths; they do not turn arbitrary host code into a security boundary.

MemoWeft is licensed under the [MIT License](./LICENSE).
