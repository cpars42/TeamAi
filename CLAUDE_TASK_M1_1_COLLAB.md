# Upgrade Spec — M1.1: Two‑Agent Collaboration (Bounded Back‑and‑Forth)

**Audience:** Claude (coder)  
**Author:** Planner  
**Date:** 2025‑08‑13

---

## 0) Context (What Already Works in M1)
- A self‑hosted single‑room web UI with a Router and two connectors: **GPT** (OpenAI) and **Claude** (Anthropic).
- User selects a target (`@gpt` or `@claude`), sends a message, and the Router forwards it as an `agent_call`.
- A single **handoff** from one bot to the other is supported, then the system **stops**.

**Observed limitation:** Conversations are effectively **one‑directional**. The second bot does not “see” the first bot’s output, and the Router stops after a single hop.

**Goal of M1.1:** Enable bounded, turn‑based collaboration between GPT and Claude (e.g., up to 6 rounds) **with transcript context** so each agent sees the latest messages and can progress the task collaboratively.

---

## 1) Scope (M1.1)
**In scope:**
- Add a **collaboration session** concept with `session_id`, `round`, and `max_rounds`.
- Pass a **recent transcript window** + **goal** to each model call.
- Replace the ad‑hoc handoff with a **single JSON reply envelope** that supports both “message” and optional “handoff” and “final” flags.
- Update Router to run a **bounded turn loop** (e.g., up to `max_rounds`), alternating between GPT and Claude based on the envelope.
- UI toggle for **Collaborate** mode and a **Stop** button.

**Out of scope (M1.1):**
- Persistent storage, vector DB, memory, multi‑rooms, auth, token streaming.

---

## 2) Event Contract (Room Protocol Additions)

Continue using your existing event fields from M1 and add the following when running a collaboration session:

```jsonc
{
  "type": "human_message | agent_call | agent_response",
  "sender": "you | router | gpt | claude",
  "target": "router | gpt | claude | all",
  "thread": "default",
  "text": "visible text",
  "call_id": "uuid",
  "ts": 1700000000,

  // NEW for collaboration mode:
  "session_id": "uuid",    // collaboration session identifier
  "round": 1,              // current round number (starts at 1)
  "max_rounds": 6          // cap to prevent loops
}
```

**Session state (in memory):**
```
sessions[session_id] = {
  "participants": ["gpt","claude"],
  "goal": "<original user goal>",
  "max_rounds": 6,
  "round": 1,
  "transcript": [ { "role": "gpt|claude|user|router", "name": "...", "text": "..." }, ... ]
}
```

The **transcript** is a rolling window of recent items used for context (see §5).

---

## 3) Unified JSON Reply Envelope (Required From Both Connectors)

**Every** agent response in collaboration mode must be strict JSON in this shape:

```json
{
  "message": "Visible content for the room (agent's reply).",
  "handoff": {
    "to": "gpt" | "claude",
    "task": "Short instruction for the next agent."
  },
  "final": false
}
```

- `message` (string) — content to display in the room. **Required.**
- `handoff` (object, optional) — if present, instructs the Router who should speak next and what to do.
- `final` (bool, optional) — if `true`, collaboration ends immediately (ignore any `handoff`).

**JSON Schema (use with OpenAI Structured Outputs; validate for Anthropic too):**

```json
{
  "type": "object",
  "properties": {
    "message": { "type": "string", "minLength": 1 },
    "handoff": {
      "type": "object",
      "properties": {
        "to": { "type": "string", "enum": ["gpt", "claude"] },
        "task": { "type": "string", "minLength": 1, "maxLength": 500 }
      },
      "required": ["to", "task"],
      "additionalProperties": false
    },
    "final": { "type": "boolean" }
  },
  "required": ["message"],
  "additionalProperties": false
}
```

**Validation rule:** If parsing fails or the shape is invalid, treat the raw output as a plain `message` (no handoff, not final). Log a warning.

---

## 4) System Prompts (Per Connector)

**Shared rules (identical for GPT & Claude):**
1. You are collaborating with another assistant in a turn‑based session managed by a router.
2. Return **only** strict JSON matching the provided schema: `{message, handoff?, final?}`.
3. If you want the other assistant to go next, include a concise `handoff` with `to` and a one‑sentence `task`.
4. If the solution is complete, set `final: true` and omit `handoff`.
5. Be concise and move the task forward each turn.

**Append a name hint to each agent:**
- GPT prompt: *Your name is `gpt`.*
- Claude prompt: *Your name is `claude`.*

**Context to include in each model call:**
- **Goal** (original user request)
- **Recent transcript window** (last 6–10 items; trim to fit provider limits)
- **Round and max_rounds** (e.g., “Round 3 of 6”)
- Final instruction: *“Return JSON only per schema—no extra text.”*

---

## 5) Transcript Window (Context Packaging)

Maintain a rolling `transcript` inside the session state. For each agent call, pass:
- The **goal** (verbatim from the user),
- The last **8** transcript items (or ~2–3K tokens), oldest‑first,
- Optionally, convert `handoff.task` into a transcript item with role `router` addressed to the next agent.

If the prompt risks exceeding token limits, truncate the oldest transcript entries first.

---

## 6) Router Changes (Turn‑Taking Controller)

**Entry points to start collaboration:**
- UI toggle: **Mode = Collaborate (gpt↔claude)** with `max_rounds` (default 6), initial `speaker` (user‑selected), and `goal` (the user text), or
- Command (optional): `@router collaborate gpt claude: <goal>`.

**Turn loop (conceptual):**
1. Initialize `session_id`, `round=1`, `speaker=<initial target>`.
2. While `round <= max_rounds`:
   - Build context: `goal`, transcript window, round info, schema instruction.
   - Call **speaker** connector; parse JSON envelope.
   - Broadcast the `message` as an `agent_response`; append to transcript.
   - If `final == true`: end session (reason: final) → stop.
   - Else if `handoff` present:
     - Set `speaker = handoff.to`,
     - Append handoff `task` into transcript as a router instruction to next agent,
     - `round += 1` and continue.
   - Else (no handoff): end session (reason: no handoff) → stop.
3. If loop exits by `round > max_rounds`: end session (reason: cap reached).

**Loop guard:** Collaboration ends on `final`, absent `handoff`, or `max_rounds` reached. No infinite ping‑pong.

---

## 7) UI Adjustments
- Add **Mode** toggle: `Single call` | `Collaborate (gpt↔claude)`.
- When in collaborate mode, show a small status chip: **“Collab: 0/6”**, incrementing each round.
- Add a **Stop** button that ends the session immediately; Router posts “Collaboration canceled by user.”

---

## 8) Acceptance Tests (Definition of Done)

**A. Context sharing works**
- Start collaboration with goal: “Write a 2‑paragraph fairy tale—Claude drafts, GPT edits, alternate until done (≤4 rounds).”
- Expected: Claude drafts para 1 → GPT edits and requests next → Claude finalizes → GPT sets `final: true`. No “Which story?” responses.

**B. Bounded rounds**
- Set `max_rounds=2`, request multi‑step task.
- Expected: exactly 2 turns then end (cap reached) if neither agent set `final: true`.

**C. Mixed envelope behavior**
- Agent returns `message` + `handoff`: Router displays and continues next turn.
- Agent returns `message` + `final: true`: Router displays and ends session immediately.

**D. Malformed JSON handling**
- If an agent emits non‑JSON or wrong shape, Router logs a warning, treats content as plain `message`, **ignores handoff**, ends session.

**E. Manual stop**
- Clicking **Stop** ends the session; Router posts a cancellation notice.

---

## 9) Connector Updates (Small but Important)
- Both connectors must:
  - Accept **goal + transcript + round/max_rounds** as inputs.
  - Return the **JSON envelope** (`{message, handoff?, final?}`).
  - For OpenAI, use Structured Outputs (JSON schema in §3).  
  - For Anthropic, enforce “JSON only” via instructions and validate/repair if needed.

---

## 10) Deliverables
- Updated code implementing the collaboration mode as described.
- **README** section for M1.1: how to start a collaboration session, the JSON envelope, and the new UI controls.
- Demonstration using the Acceptance Tests in §8 (copy the exact prompts below).

---

## 11) Test Prompts (Use Exactly These)

**Direct kickoff (collaborate mode on):**
1) *“Collaborate to outline and refine a 5‑step plan for a weekend hiking trip. Keep it concise.”*  
   - Expect alternation and eventual `final: true` within ≤6 rounds.

2) *“Write a 2‑paragraph fairy tale—Claude drafts, GPT edits, alternate until done (≤4 rounds).”*

**Edge cases:**
3) *“Summarize this in 1 sentence, then stop.”* (Agent should return `final: true` in first reply.)  
4) Send two collaboration requests back‑to‑back; ensure sessions do not interfere.  
5) Force malformed JSON (e.g., prompt the model to include prose) and verify graceful fallback.

---

## 12) Non‑Goals (unchanged from M1)
- No persistence, vector DB, memory, multi‑rooms, auth, or streaming in this milestone.

---

## 13) Risks & Mitigations
- **Models outputting non‑strict JSON:** Validate against schema; on failure, treat as plain `message` and stop session. Keep prompts short and explicit: “Return JSON only per schema—no extra text.”
- **Runaway loops:** Enforced by `max_rounds` and the Router loop guard.
- **Context overflow:** Hard‑limit transcript window (e.g., 2–3K tokens). Truncate oldest items first.

---

## 14) Handoff from M1 to M1.1 (Implementation Checklist)
1. Add session state (`session_id`, `round`, `max_rounds`, `transcript`, `goal`).  
2. Add UI toggle “Collaborate”, a rounds indicator, and a Stop button.  
3. Change connectors to output the **unified JSON envelope**.  
4. Pass **goal + transcript + round info** into every model call.  
5. Replace one‑hop logic with the **bounded turn loop** in Router.  
6. Validate with the Acceptance Tests in §8.  
7. Update README with usage and examples.

---

## 15) What to Hand Back
- Code changes implementing M1.1 (collaboration mode).  
- Updated README with run instructions and the Acceptance Tests above.  
- A brief note on any deviations and known edge cases.
