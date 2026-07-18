# MemoWeft Boundaries: Core, Host, and Plugins

**English** | [简体中文](./boundaries.zh-CN.md)

MemoWeft separates memory-library responsibilities from application and extension responsibilities. This page describes the public boundary for the built-in Core APIs and `PluginContext`; it is not a security boundary around arbitrary host or plugin code.

See the [architecture overview](./architecture.md), the [plugin contract](../plugin-contract.md), and the [Memory Surface Contract](../reference/memory-surface-contract.md).

## At a glance

| Layer  | Primary responsibility                                                |
| ------ | --------------------------------------------------------------------- |
| Core   | Memory data, processing, and public memory APIs.                      |
| Host   | Product behavior, operations, consent, and security controls.         |
| Plugin | Optional capabilities exposed through supported extension interfaces. |

## Core

Core is the memory library that a host application imports. It owns the evidence, event, and cognition data model; ingestion, recall, and memory-management APIs; provenance and confidence processing; migrations, integrity checks, and portable bundles; and model and retrieval abstractions.

Core is headless. It does not provide a chat product or UI, define a host's privacy policy, collect operating-system data, load plugins dynamically, or schedule `updateProfile()` automatically. Hosts choose when and how to call that one-shot operation.

The built-in Core paths preserve the library's data and processing rules. A host using custom storage, ingestion, or data-forwarding paths is responsible for applying equivalent safeguards where appropriate.

## Host

The host is the application that configures and uses Core. `apps/memoweft-host` is a reference implementation in this repository.

A host owns Core lifecycle and storage configuration; product UI and conversation behavior; consent and user-facing privacy notices; authentication, authorization, transport, storage, and tenant isolation; profile-update scheduling; memory-management flows; and plugin registration and policy.

Hosts should use Core's public APIs for memory operations and validate data that enters through their own integrations. They also decide whether a plugin is suitable to install and which permissions or product capabilities it receives.

## Plugin

Plugins add optional experiences, tools, or observation collection. Supported plugins can observe lifecycle hooks and use the capabilities granted through `PluginContext`, such as submitting an observation or requesting recalled memory. They may also provide capabilities that a host integrates into its product.

`PluginContext` exposes selected Core capabilities rather than a store API. This limits the built-in extension surface, but it does not sandbox arbitrary plugin code. Hosts remain responsible for the execution environment, plugin trust decisions, consent, and any security controls needed for plugin or host code.

## Authorization flags and security controls

Evidence flags such as `allowLocalRead`, `allowCloudRead`, and `allowInference` affect MemoWeft's prompt-selection and inference behavior on its built-in paths. They are not access control, encryption, or a general-purpose data-security mechanism. Hosts must enforce their own controls for storage, transport, access, tenant isolation, and any data forwarded outside those paths.

## Common flows

```text
User / UI
   ↓
Host ── public API ──→ Core
 ↑                       │
 └──── result / recall ──┘
```

```text
Collector Plugin
   ↓ observation request
Host policy and consent check
   ↓ core.ingestObservation()
Core evidence pipeline
```

```text
User memory-management action
   ↓ confirmation and reason
Host
   ↓ core.memory.*
Core validation, mutation, and audit metadata
```

These flows show the usual integration points. Custom integrations may need additional host-side validation, authorization, and operational controls.
