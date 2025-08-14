# M1.2 — Autopilot Collaboration (Run Until “Allstop”)

**Audience:** Claude (coder)  
**Author:** Planner  
**Purpose:** Upgrade the existing collaboration mode so GPT and Claude continue taking turns **indefinitely** until the user explicitly stops the session by saying **“Allstop”** (or pressing a Stop button).

---

## 0) Current State (from M1.1)
- Two agents (GPT via OpenAI, Claude via Anthropic) can collaborate with a **bounded** number of rounds.
- Router passes a **recent transcript** and **goal** each turn; agents reply using a **JSON envelope**: `{message, handoff?, final?}`.
- Router stops on `final:true`, lack of `handoff`, or after `max_rounds`.

**New requirement (M1.2):** Allow **continuous collaboration** (no fixed max rounds) and stop **only** when the user says **“Allstop”** (case‑insensitive) or clicks **ALLSTOP** in the UI.

---

## 1) Scope (M1.2)
**In scope**
- Add **Autopilot** mode (unbounded collaboration).
- Recognize **“Allstop”** as a user command to immediately terminate the active collaboration session in the current thread.
- Keep context packaging from M1.1 (goal + recent transcript window).
- Keep the same **reply envelope** for agents.

**Out of scope**
- Persistence, vector DB, memory, multi‑room, auth, token streaming (future milestones).

---

## 2) Control Model
### 2.1 Modes
- **Single Call** — unchanged from M1.
- **Collaborate (bounded)** — unchanged from M1.1 (you can keep this for testing).
- **Autopilot (unbounded)** — **new**; the router continues turn‑taking until user stops.

### 2.2 Stop Triggers (in priority order)
1. **User command** “Allstop” (see §3) or UI Stop button → **end session now**.
2. **Emergency fail‑safes** (see §6) → end session if exceeded (rare, but required).
3. (Optional) Manual **Pause**/Resume (not required for M1.2).

> In **Autopilot**, ignore `final:true` for stopping (log it but keep going) **unless** the user has enabled “Respect final” in settings (optional flag; default off).

---

## 3) “Allstop” Command (Router Behavior)
### 3.1 Recognition
- Treat **“Allstop”** as case‑insensitive and forgiving of spacing/hyphenation: match `allstop`, `all stop`, `all-stop`.
- Accept both:
  - A typed message (as a regular `human_message`), and
  - A dedicated **Stop** button in the UI (preferred).

### 3.2 Semantics
- If a collaboration session is **active** in the current thread:
  - Mark session state `status = "stopped_by_user"`.
  - Set a `stop_requested = True` flag **immediately**.
  - If a model call is **in flight**, allow it to complete quietly but **discard** its response (do not post it). If cancellation is available, attempt it.
  - Broadcast a `system` event: “Collaboration stopped by user (Allstop).”
- If no active session, ignore gracefully.

---

## 4) Turn‑Taking in Autopilot
Use the same JSON envelope from M1.1:
```json
{ "message": "...", "handoff": { "to": "gpt|claude", "task": "..." }, "final": false }
```

**Next‑speaker policy (Autopilot):**
1. If `handoff` present → next speaker is `handoff.to`; append `handoff.task` to transcript as a router instruction.
2. If **no `handoff`** → **alternate** to the other agent by default.
3. Ignore `final:true` in Autopilot (log: “agent suggested finish”) unless the optional “Respect final” setting is **on**.

The router loops forever (no `max_rounds`) until §3 or §6 triggers termination.

---

## 5) Transcript & Prompting
- Keep the **goal** constant for the session.
- Maintain a rolling **transcript**; per turn, send the **last 8 items** (or ~2–3K tokens) + “Round: <n> (Autopilot)”.  
- Trim oldest messages first to stay under token limits.
- Keep the system prompt from M1.1: “Return JSON only per schema—no extra text.”

---

## 6) Fail‑Safes (Required)
To avoid runaway cost or meaningless loops, implement **soft** and **hard** guards:

- **Soft warn:** After every 25 turns or ~50k tokens, post a small system note: “Autopilot running. Say ‘Allstop’ to end.” (Does **not** stop.)
- **Hard cutoffs (emergency only):**
  - `max_turns_emergency`: default 200 (then stop with reason “emergency cap”).
  - `max_tokens_emergency`: default 200k aggregated prompt+completion tokens (then stop).
  - `max_elapsed_minutes`: default 20 (then stop).
- When an emergency cutoff fires, broadcast a system notice explaining which guard tripped.

(Values should be configurable; defaults above are suggestions.)

---

## 7) UI Changes
- Add a **mode toggle** with three choices: `Single`, `Collaborate`, `Autopilot`.
- Show a **status chip** while Autopilot is running: e.g., “Autopilot: running (turn 17)”.  
- Add a **red ALLSTOP button** that sends the stop command immediately.
- When an agent emits `final:true`, surface a subtle banner: “Agent suggests finish — press ALLSTOP to end or let them continue.”

---

## 8) Connector Requirements (unchanged interface)
- Continue returning the **JSON envelope** `{message, handoff?, final?}`.
- Continue receiving **goal + transcript window + mode label (“Autopilot”)** in the prompt.
- Keep outputs concise; move the task forward each turn.

---

## 9) Router Algorithm (Autopilot Mode, Conceptual)
```
start session_id; round = 1; speaker = user-selected initial agent
while not stop_requested:
    build context: goal + transcript window + "Autopilot"
    call speaker; parse JSON envelope
    if stop_requested: break
    if envelope invalid: message = raw_text; handoff = None; final = False
    post message (agent_response) to room; append to transcript
    if soft_warn thresholds reached: post reminder (“Say Allstop to end”)
    if hard_guard exceeded: stop(reason="emergency"); break
    if handoff: speaker = handoff.to; append handoff.task (router instruction)
    else: speaker = other_agent(speaker)  # alternate by default
    round += 1
broadcast system: “Collaboration stopped” with reason (Allstop / emergency)
```

---

## 10) Acceptance Tests (Definition of Done)
**A. Autopilot runs past former bounds**
- Start Autopilot with goal: “Brainstorm features for a cozy bakery website; keep alternating improvements indefinitely.”  
- Observe >10 turns without stopping.

**B. Handoff & alternation**
- Force a turn with **no `handoff`** from an agent → Router alternates to the other agent automatically.

**C. Allstop command (typed)**
- Type “Allstop” (any case; with/without space) → Router ends within 1s; last in‑flight response is ignored; system notice is posted.

**D. ALLSTOP button**
- Click button → identical behavior to typed command.

**E. Final ignored (by default)**
- If an agent returns `final:true`, Router keeps going and displays a banner suggesting the user may stop.

**F. Emergency cutoff**
- Configure a tiny `max_turns_emergency=3` for test; verify the Router stops with an explanatory system message.

**G. No active session**
- Send “Allstop” when no session is active → no crash; log “no-op”.

---

## 11) Deliverables
- Updated Router implementing **Autopilot** mode, stop command, and guards.
- UI updates (mode toggle, status chip, ALLSTOP button, optional banner).
- README updates:
  - How to start Autopilot.
  - How to stop via Allstop.
  - Guardrail settings and defaults.
  - The JSON reply envelope and examples.
- Short note describing how in‑flight responses are discarded after Allstop.

---

## 12) Implementation Notes
- Stop command can arrive at any time; check a shared `stop_requested` flag **before** posting each new event.
- If you cannot cancel an HTTP call mid‑flight, **discard** the result after Allstop.
- Normalize the text command by lowercasing and stripping non‑letters to match `allstop` (so “All stop”, “ALL-STOP” work).
- Keep the transcript window small to control token usage.
- Consider logging a **session summary** upon stop to help with future memory features.
