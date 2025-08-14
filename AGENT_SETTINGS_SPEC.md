# Spec — Per‑Agent Settings (Roles & Prompts)

**Audience:** Claude (coder)  
**Author:** Planner  
**Date:** 2025‑08‑13

Goal: Add a **Settings page per agent** (GPT and Claude, later any agent) where the operator defines each agent’s **role**, **system prompts**, **behavioral knobs**, and **output schema**. Changes apply without server restarts and are scoped to modes (Single, Collaborate, Autopilot).

---

## 1) Scope
- New **Settings UI** (per agent) with live preview and validation.
- A durable **settings store** on disk (JSON or YAML) with atomic writes.
- Runtime **config loader** with hot‑reload + versioning (rollback).
- Router & connectors consume these settings at call time.
- Safeguards for structured outputs (schema), safety toggles, and rate/budget limits.

Out of scope: user auth, multi‑tenant roles (future).

---

## 2) Information Architecture

### 2.1 Agents
- Start with two agents: `gpt` and `claude`.
- Each agent is an instance of the **Agent Profile** (below).
- Allow **Add Agent** future‑proofing (id, name, provider).

### 2.2 Agent Profile (data model)
```jsonc
{
  "id": "gpt",
  "display_name": "ChatGPT",
  "provider": "openai",                // or "anthropic", "ollama", etc.
  "model": "gpt-4o-mini",
  "temperature": 0.4,
  "max_output_tokens": 800,
  "rate_limits": { "rpm": 60, "tpm": 120000 },   // optional
  "safety": {
    "allow_tools": false,
    "json_only": true,                 // enforce JSON-only replies when set
    "forbid_code": false,              // if true, agent avoids emitting code
    "disallowed_topics": []            // optional list of strings
  },
  "modes": {
    "single": {
      "system_prompt": "…",
      "output_schema": null            // or JSON Schema object
    },
    "collaborate": {
      "system_prompt": "…",
      "output_schema": { /* schema for {message, handoff?, final?} */ }
    },
    "autopilot": {
      "system_prompt": "…",
      "output_schema": { /* schema for {message, handoff?, final?} */ }
    }
  },
  "placeholders": [ "GOAL", "ROUND", "TRANSCRIPT", "YOUR_NAME", "PEER_NAME" ],
  "notes": "freeform notes for operator",
  "version": 3,                         // incremented on save
  "updated_at": "2025-08-13T16:30:00Z"
}
```

### 2.3 Settings Store
- Directory: `config/agents/`
  - One file per agent: `gpt.json`, `claude.json`, etc.
  - Keep a `history/` subfolder with timestamped snapshots for rollback.
- Writes are **atomic**: write to `tmp`, fsync, then move.
- A top‑level `config/index.json` lists active agents and default models.

---

## 3) UI Requirements

### 3.1 Navigation
- Settings → Agents → {Agent Name}
- Tabs: **Role & Prompts**, **Behavior**, **Schema**, **Preview**, **Advanced**

### 3.2 Role & Prompts
- Fields:
  - **Display Name** (text)
  - **Role Summary** (short description chip displayed in room)
  - **System Prompt** (multi‑line editor; supports placeholders like `{GOAL}`, `{TRANSCRIPT}`, `{ROUND}`, `{YOUR_NAME}`, `{PEER_NAME}`)
  - **Mode Overrides**: separate editors for Single, Collaborate, Autopilot
- Controls:
  - **Insert Placeholder** dropdown to insert `{…}` tokens
  - **Revert to Default** button per mode
  - **Version Diff** view (previous vs current)

### 3.3 Behavior
- **Model** (select), **Temperature**, **Max Output Tokens**
- **Safety Toggles**: JSON‑only, forbid code, disallowed topics list
- **Rate/Budget** (optional): RPM/TPM ceilings; per‑session token budget

### 3.4 Schema
- **Output Schema Editor** (JSON) per mode
- **Validate** button: checks JSON Schema validity
- **Test Sample**: enter a fake model output; validate against schema

### 3.5 Preview
- Compose a dry‑run prompt using:
  - Goal (text), Round (int), Transcript (textarea with 3–5 sample lines)
  - Select Mode (Single/Collaborate/Autopilot)
- **Render Preview Prompt** (what will be sent as system/instructions + context)
- **Simulate Response**: show how router will parse envelope (no live API call)

### 3.6 Advanced
- Provider‑specific headers (e.g., logit biases or JSON mode flags)
- Fallback model list (e.g., try `gpt-4o-mini` then `gpt-4o`)
- Connection test

### 3.7 Global Actions
- **Save** (validates + writes file)
- **Discard**
- **Export** (download JSON)
- **Import** (upload JSON; validate before applying)
- **Rollback** (select version from history; confirm)

---

## 4) Runtime Integration

### 4.1 Hot Reload
- Settings manager watches `config/agents/*.json` for changes.
- On change, parse & validate, bump an in‑memory **version registry**.
- Router & connectors read the **latest version** at each call.

### 4.2 Prompt Templating
- Use `{PLACEHOLDER}` tokens replaced at call time:
  - `{GOAL}`, `{TRANSCRIPT}`, `{ROUND}`, `{YOUR_NAME}`, `{PEER_NAME}`
- Validation:
  - Unknown placeholders raise a warning but do not block save.
  - Missing required placeholders (for modes that require them) block save.

### 4.3 Structured Outputs
- If `json_only: true` or `output_schema` present:
  - OpenAI: enable **Structured Outputs** with the provided schema.
  - Anthropic: enforce a “JSON‑only” instruction; parse and validate.
- On schema validation failure:
  - Treat output as plain `message` if safe; else surface error per policy.

### 4.4 Safety Enforcement
- If `forbid_code: true`, append “Do not output code snippets.” to the prompt.
- If `disallowed_topics` non‑empty, inject a pre‑check instruction to avoid or generalize those topics.
- Router double‑checks and can redact before posting (simple regex for code blocks).

### 4.5 Mode Selection
- Router passes `mode` to connectors (`single`, `collaborate`, or `autopilot`).
- Connector chooses the **mode‑specific** prompt & schema from the agent profile.

---

## 5) Validation Rules

- **Model sanity**: provider+model allowed; otherwise block save with clear error.
- **Schema sanity**: valid JSON, no trailing text, `additionalProperties` considered.
- **Prompt length**: warn if system prompt > 4k characters.
- **Placeholders**: warn on unknown placeholders; require `{YOUR_NAME}` for clarity.

---

## 6) Defaults to Preload

Provide sensible defaults so the app works immediately:

- **GPT (ChatGPT)**
  - Role: “Editor & Structurer”
  - Single: simple helpful assistant prompt
  - Collaborate & Autopilot: **JSON envelope schema** for `{message, handoff?, final?}`
  - Temperature 0.4, Max Output 800

- **Claude**
  - Role: “Drafter & Critic”
  - Single: simple helpful assistant prompt
  - Collaborate & Autopilot: same JSON envelope schema
  - Temperature 0.4, Max Output 800

Load schema from `config/schemas/envelope.json` (single source of truth).

---

## 7) API Endpoints (internal)

- `GET /api/settings/agents` → list of agent ids + versions
- `GET /api/settings/agents/{id}` → full profile JSON
- `POST /api/settings/agents/{id}` → validate + save (atomic write + history)
- `POST /api/settings/agents/{id}/validate` → dry validation (no write)
- `POST /api/settings/agents/{id}/rollback` → {version} → restore from history
- `POST /api/settings/preview` → returns rendered prompt with placeholders resolved (no provider call)

_All endpoints local‑only in M1.x; secure later._

---

## 8) Acceptance Criteria

1) **Edit & Save**: Operator can change system prompts per mode and save; changes apply to the **next** model call without restarts.
2) **Schema Validation**: Invalid schema blocks save with clear errors; valid schema enables Structured Outputs for GPT and JSON‑only enforcement for Claude.
3) **Preview Render**: The preview shows the exact system text that will be sent, with placeholders resolved (Goal/Transcript/Round).
4) **Rollback**: Operator can revert to any prior version; version/date shown.
5) **Router Consumption**: Router uses the updated prompts/schemas on the next turn; collaboration works as before.
6) **Safety Toggles**: Toggling `json_only` or `forbid_code` affects outputs accordingly (verified manually in Preview + live test).
7) **Import/Export**: Profiles export/import cleanly; bad imports are rejected with actionable errors.

---

## 9) Deliverables

- Settings UI and backend as described.
- `config/agents/{gpt,claude}.json` with defaults.
- `config/schemas/envelope.json` (single shared schema for collaboration/autopilot).
- README updates: how to edit prompts, validate schemas, preview, rollback.
- Smoke tests covering: save→reload, schema validation, preview render.

---

## 10) Future Extensions (not required now)
- Per‑thread overrides; per‑user preference layers.
- Role templates (starter kits: “Planner”, “Coder”, “Critic”, “Teacher”).
- Secret management (provider keys) separate from prompts.
- Multi‑room and RBAC for multi‑user environments.
