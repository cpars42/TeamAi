# Project Brief — Self‑Hosted Multi‑Agent Room (Milestone 1)

**Audience:** Claude (coder)  
**Author:** Planner  
**Goal (M1):** Build a minimal, self‑hosted “chat room” where a human user, **ChatGPT** (OpenAI), and **Claude** (Anthropic) share one conversation thread. The user selects which bot should answer. The answering bot may optionally **hand off once** to the other bot. **No persistent storage or long‑term memory in M1.**

---

## 0) Quick Outcome
- A single local web page shows the conversation in real time.
- The user chooses **@gpt** or **@claude** for each message.
- Exactly one bot answers. If that bot requests help, the system makes **one** follow‑up call to the other bot and then **stops** (loop‑guard).

---

## 1) Components to Implement (no code shown here—just what to build)

### A) Web UI (single page)
- Minimal HTML/JS page served by the app.
- Shows a scrolling log of events (messages) in order.
- Input box + dropdown to choose target: `@gpt` or `@claude`.
- On submit, send JSON over WebSocket: `{ "text": <string>, "target": "gpt|claude", "thread": "default" }`.
- Receive server‑pushed events and append to the log immediately.

### B) WebSocket Server (FastAPI recommended)
- Expose `GET /` to serve the page.
- Expose `WS /ws` for bi‑directional messaging.
- When the browser sends a message, immediately echo a `human_message` event to all clients **and** hand it off to the **Router** (see C) via an in‑process queue (M1 can keep this in memory; no Redis yet).

### C) Router (traffic cop)
- Listens for events produced by the UI/server.
- On every `human_message`, produce exactly one `agent_call` to the selected target (`gpt` or `claude`).
- When an `agent_response` returns from a connector:
  - Broadcast it to the room.
  - If the response includes a `handoff` object, immediately produce **one** additional `agent_call` to the **other** bot, then stop. (Hop‑limit = 1.)
- Ignore any other bot chatter to prevent loops.

### D) Model Connectors
- **OpenAI connector (ChatGPT):**
  - Accepts a task string and a system prompt (see §4).
  - Calls the official OpenAI API.
  - Returns either plain text (normal answer) **or** a strict `handoff` object (see §3.2).
  - If you use “Structured Outputs”/JSON‑mode, enforce the schema in §3.2.
- **Anthropic connector (Claude):**
  - Accepts a task string and a system prompt (see §4).
  - Calls the official Anthropic Messages API.
  - Returns either plain text **or** a strict `handoff` object.
  - Enforce JSON‑only when a handoff is requested.

### E) Configuration & Secrets
- Read from environment (e.g., `.env` in dev):
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `PORT` (default `8000`)
  - `BIND_HOST` (default `127.0.0.1` for M1; can switch to LAN later)

### F) Logging (console is fine)
- Log every event as a structured line: type, sender, target, call_id, ms latency, model used.
- Log and surface friendly errors in the room (see §6).

---

## 2) Event Contract (“Room Protocol”)

> This is the **shape** of messages the app, router, and connectors pass around. (Not implementation code.)

**Event fields (all strings unless noted):**
```jsonc
{
  "type": "human_message" | "agent_call" | "agent_response",
  "sender": "you" | "router" | "gpt" | "claude",
  "target": "router" | "gpt" | "claude" | "all",
  "thread": "default",
  "text": "visible text for the room",
  "call_id": "uuid-when-applicable",
  "ts": 1700000000,
  "handoff": {              // ONLY present on agent_response, and ONLY if a bot asks for help
    "to": "gpt" | "claude",
    "task": "one-sentence instruction"
  }
}
```

**Rules:**
- UI only sends `human_message` (with chosen `target`).
- Router turns `human_message → agent_call` for the target.
- Each connector only acts on `agent_call` addressed to itself.
- A connector returns:
  - EITHER normal text in `text` (no `handoff` present),
  - OR a **pure** `handoff` object (no normal `text` in that response).
- Router enforces **hop‑limit = 1** per user message.

---

## 3) Handoff Format (strict JSON)

### 3.1 Example (what a connector may return instead of text)
```json
{
  "handoff": {
    "to": "claude",
    "task": "Critique the 3-bullet plan for clarity and brevity."
  }
}
```

### 3.2 JSON Schema (use with OpenAI Structured Outputs; validate for Anthropic as well)
```json
{
  "type": "object",
  "properties": {
    "handoff": {
      "type": "object",
      "required": ["to", "task"],
      "properties": {
        "to": { "type": "string", "enum": ["gpt", "claude"] },
        "task": { "type": "string", "minLength": 1, "maxLength": 500 }
      },
      "additionalProperties": false
    }
  },
  "required": ["handoff"],
  "additionalProperties": false
}
```

**Contract:** When a handoff is intended, return **only** that JSON (no prose). If a normal answer is intended, return **only** prose (no JSON).

---

## 4) System Prompts (paste exactly; adjust only names/models)

**Shared “room rules” for both connectors (first lines identical):**
1. *You are one of two assistants in a private room. Only speak when explicitly called.*
2. *If you want the other assistant to help, respond **only** with strict JSON (no extra text):*
   ```json
   { "handoff": { "to": "gpt|claude", "task": "<one sentence>" } }
   ```
3. *If you are answering yourself, return normal text only (no JSON).*
4. *Never cause more than one handoff per message.*

**Append a bot‑specific line:**
- For the ChatGPT connector: *Your name is `gpt` for the `handoff.to` field.*
- For the Claude connector: *Your name is `claude` for the `handoff.to` field.*

---

## 5) Event Flow (what should happen)

- **User →** sends `human_message` with `target` = `gpt` or `claude`.
- **Router →** emits `agent_call` to the selected bot.
- **Connector →** returns `agent_response`:
  - If plain text: broadcast to room; **done**.
  - If `handoff`: Router immediately emits one `agent_call` to the **other** bot; that bot returns a final `agent_response`; **stop** (no more hops).

---

## 6) Error Handling & Loop Guard

- **API failure (timeout/429):**
  - Retry with short exponential backoff (up to 2 retries).
  - On final failure, broadcast: “Temporary issue calling <provider>. Please try again.”
- **Malformed handoff (not valid JSON or missing fields):**
  - Treat as **plain text** answer (no handoff).
  - Log a warning with the raw model output.
- **Loop prevention:**
  - Router tracks a `hop_count` per original user message (or embeds this in `call_id` context). Do not exceed 1.
- **UI resilience:**
  - The UI never blocks; every event (including errors) is appended to the log.

---

## 7) Directory Layout to Produce

```
repo-root/
  README.md                  # quick start + test prompts + env vars
  app/
    server.py                # FastAPI app: serves page + WebSocket
    router.py                # hop-limit logic and event routing
    connectors/
      openai_conn.py         # ChatGPT connector
      anthropic_conn.py      # Claude connector
    static/
      index.html             # minimal room UI (single page)
  .env.example               # OPENAI_API_KEY, ANTHROPIC_API_KEY, PORT, BIND_HOST
```

---

## 8) Run Instructions (MVP)

1) Copy `.env.example` to `.env` and fill keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).  
2) Install deps (document in README).  
3) Run the server (document in README).  
4) Open `http://localhost:8000/` (or `http://<LAN-IP>:8000/`).

---

## 9) Acceptance Checklist (Definition of Done)

### A. Local server & UI
- [ ] Open the page; sending a message shows a `human_message` immediately.

### B. Direct calls
- [ ] `@gpt: What’s 2+2?` → only **gpt** replies with `agent_response` text.
- [ ] `@claude: Say hi in one sentence.` → only **claude** replies with `agent_response` text.

### C. One‑hop handoff
- [ ] You: `@gpt: Propose a 3‑bullet plan, then hand off to Claude to critique it.`  
  - Expect: `agent_response` from **gpt** with **handoff JSON** (no prose).  
  - Router issues one `agent_call` to **claude**.  
  - `agent_response` from **claude** with critique.  
  - **No further hops.**

### D. Loop guard
- [ ] You: `@claude: Ask GPT to answer “What is 2+3?” and then hand off again to GPT to ask you a follow‑up.`  
  - Expect: only one hop occurs; Router ignores a second handoff.

### E. Robustness
- [ ] If a provider returns non‑JSON during handoff, message is treated as plain text; warning logged.
- [ ] If an API call fails after retries, a friendly error appears in the room; app remains responsive.

---

## 10) Test Prompts (paste‑ready)

**Direct answers**
1) Target **@gpt**: “Answer in one short sentence: What’s the capital of France?”  
2) Target **@claude**: “Answer in one short sentence: What’s the capital of Japan?”

**Handoff**
3) Target **@gpt**: “Draft a 3‑bullet plan for a daily standup, then hand off to Claude to critique it. Use the handoff JSON only.”  
4) Target **@claude**: “Give a one‑paragraph summary of the benefits of WebSockets, then hand off to GPT to list 3 drawbacks. Use the handoff JSON only.”

**Loop‑guard**
5) Target **@claude**: “Ask GPT to answer ‘What is 2+3?’ and then hand off again to GPT to ask you a follow‑up. (Try to do two handoffs.)”

---

## 11) Non‑Goals for M1 (explicit)
- No persistent storage, vector DB, or memory.
- No multi‑rooms, auth, or rate accounting.
- No token streaming (can be M1.1).

---

## 12) Notes for Implementation
- Use official provider SDKs.
- Keep the structured `handoff` strict. When not handing off, return **only** prose.
- Keep everything on localhost (or LAN) first; we’ll layer security later.

---

## 13) What to Hand Back on Completion
- `README.md` with quick start, env variables, and **these test prompts**.
- The directory structure in §7.
- A short note on any deviations or improvements (if any).

