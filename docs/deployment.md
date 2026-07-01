# Model deployment & privacy modes · MemoWeft

> MemoWeft is cloud-friendly by default, but not cloud-blind. It should be easy to run with an OpenAI-compatible cloud endpoint, while still keeping evidence-level control over what may be sent to a cloud model.

## Positioning

MemoWeft does **not** require local models as the default path. For most host developers, the simplest way to try or integrate MemoWeft is:

1. provide an OpenAI-compatible chat endpoint;
2. optionally provide a separate small/fast write-path model;
3. optionally provide an embedding endpoint for semantic recall.

Local models remain supported, but they are an advanced deployment option rather than the default onboarding path.

The key boundary is:

- ✅ model calls may be cloud-first;
- ❌ raw evidence should not be sent to cloud blindly;
- ✅ each evidence item carries authorization bits such as `allowCloudRead`, and MemoWeft filters cloud-readable evidence before cloud LLM calls.

MemoWeft keeps the switch and filtering rules. The **host application** still owns the actual privacy policy, consent UI, and user-facing security decisions.

---

## Recommended modes

### 1. Cloud-first mode

Use this for quick demos, prototypes, and normal developer onboarding.

```ini
MEMOWEFT_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_LLM_API_KEY=sk-xxxx
MEMOWEFT_LLM_MODEL=your-chat-model

# Optional: faster/cheaper model for distill/consolidate/attribute.
MEMOWEFT_WRITE_LLM_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_WRITE_LLM_API_KEY=sk-xxxx
MEMOWEFT_WRITE_LLM_MODEL=your-small-fast-model

# Optional: semantic recall. Without it, recall degrades to empty search.
MEMOWEFT_EMBED_BASE_URL=https://your-cloud-endpoint/v1
MEMOWEFT_EMBED_API_KEY=sk-xxxx
MEMOWEFT_EMBED_MODEL=your-embedding-model
```

Best for:

- getting MemoWeft running quickly;
- integrating it into an LLM app without local model setup;
- testing the read/write path before tuning privacy rules.

Trade-off:

- anything marked `allowCloudRead=true` may be included in prompts sent to the configured cloud endpoint.

---

### 2. Cloud-guarded mode

Use this as the normal production-minded baseline: cloud models are still used, but evidence-level authorization controls what the write path may send to them.

Recommended defaults:

| Evidence source | Default cloud policy | Reason |
| --- | --- | --- |
| User chat / explicit memory | `allowCloudRead=true` by host choice | The user is already talking to an AI-powered host. |
| Manual observation approved by user | host choice | The host should expose a clear consent switch. |
| Desktop / device observations | `allowCloudRead=false` | Window titles, app usage, file paths, and device state can be sensitive. |
| Screen OCR / clipboard / files | `allowCloudRead=false` | High-risk raw private content. |
| Health / sleep / heart-rate data | `allowCloudRead=false` | Sensitive personal data. |

Best for:

- real desktop assistants;
- personal agents that ingest behavior signals;
- hosts that want cloud convenience without sending all raw observations to the cloud.

Trade-off:

- if too much evidence is marked local-only while only cloud models are configured, write-path cognition may be less complete. The host should make this visible rather than silently pretending the model saw everything.

---

### 3. Hybrid / local-sensitive mode

Use this for privacy-sensitive deployments.

Possible routing:

| Purpose | Suggested route |
| --- | --- |
| Chat quality | cloud chat model |
| Write path for non-sensitive evidence | cloud small/fast model |
| Write path for sensitive observations | local model or skipped cloud processing |
| Embeddings for sensitive memory | local embedder |
| Embeddings for low-risk memory | cloud or local embedder |

Best for:

- local-first desktop assistants;
- users who want behavior data to remain on-device;
- future hosts that mix cloud reasoning with local perception.

Trade-off:

- more setup burden: local model services, local embedding endpoints, and more explicit routing rules.

---

## Design rules

### Cloud-first is the onboarding path

A new developer should be able to clone MemoWeft, fill in cloud-compatible env vars, and see the testbench work without installing Ollama, LM Studio, or a local embedding model.

### Evidence authorization is the safety valve

The model endpoint is not the privacy policy. The evidence layer is where MemoWeft records what each item may be used for:

- `allowLocalRead`
- `allowCloudRead`
- `allowInference`

Cloud LLM calls must respect `allowCloudRead`. If an item is not cloud-readable, it should be excluded before cloud prompts are built.

### Observed behavior should be conservative by default

Behavior observations are powerful but sensitive. A host may ingest desktop, browser, device, or wearable signals, but those observations should default to local-readable and inference-allowed, not cloud-readable, unless the host explicitly asks the user.

### The host owns consent

MemoWeft is a library. It does not render the final consent UI, does not decide legal policy, and does not choose what the user is comfortable sharing. It only provides the data model and filtering hooks so the host can implement that policy honestly.

---

## README wording

Use this product sentence when explaining deployment:

> MemoWeft is cloud-friendly by default: developers can start with any OpenAI-compatible cloud endpoint. For sensitive data, MemoWeft keeps evidence-level authorization (`allowCloudRead`) so hosts can prevent local-only observations from being sent to cloud models.

Avoid these misleading framings:

- “MemoWeft is local-first only.” — too much friction for normal developers.
- “MemoWeft sends everything to the cloud.” — wrong and unsafe.
- “MemoWeft handles privacy for you.” — the host must still own policy, consent, and UI.
