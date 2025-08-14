# Multi-Agent Chat Room (Milestone 1.2)

A minimal, self-hosted chat room where a human user can interact with both ChatGPT (OpenAI) and Claude (Anthropic) in a shared conversation thread. Features single-call mode (M1), bounded collaboration (M1.1), and autopilot mode (M1.2) with unbounded collaboration until "Allstop".

## Quick Start

1. **Setup environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. **Install dependencies:**
   ```bash
   pip install fastapi uvicorn websockets openai anthropic python-dotenv
   ```

3. **Run the server:**
   ```bash
   python -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload
   ```

4. **Open in browser:**
   ```
   http://localhost:8000/
   ```

## Environment Variables

Required in `.env` file:
- `OPENAI_API_KEY` - Your OpenAI API key
- `ANTHROPIC_API_KEY` - Your Anthropic API key
- `PORT` - Server port (default: 8000)
- `BIND_HOST` - Server host (default: 127.0.0.1)

## Features

### Single Call Mode (M1)
- Choose `@gpt` or `@claude` from the dropdown
- Selected agent responds directly
- Agents can hand off once to the other agent
- Strict one-hop limit prevents loops

### Collaborate Mode (M1.1)
- Toggle "Collaborate (bounded)" mode
- Agents work together in bounded turn-taking sessions
- Each agent sees conversation context and goal
- Configurable max rounds (2-10, default 6)
- Session ends when: `final: true`, no handoff, or max rounds reached

### Autopilot Mode (M1.2) 🆕
- Toggle "Autopilot (unbounded)" mode
- **Continuous collaboration** with no round limits
- Agents alternate indefinitely until user says **"Allstop"**
- Ignores `final: true` by default (shows suggestion banner)
- Emergency safeguards prevent runaway sessions
- Real-time status: "Autopilot: running (turn X)"

## Stopping Autopilot

### Allstop Command
- **Type "Allstop"** (any case, spacing: "all stop", "ALL-STOP" work)
- **Click red "ALLSTOP" button** in UI
- Immediately terminates active collaboration
- Discards any in-flight responses

### Emergency Safeguards
- **Max turns**: 200 turns (configurable)
- **Max time**: 20 minutes (configurable)  
- **Max tokens**: 200k tokens (configurable)
- **Soft warnings**: Every 25 turns

## Collaboration JSON Envelope

In collaboration modes, agents must respond with strict JSON:

```json
{
  "message": "Visible content for the room",
  "handoff": {
    "to": "gpt|claude",
    "task": "Short instruction for next agent"
  },
  "final": false
}
```

- `message` (required): Response visible to everyone
- `handoff` (optional): Pass control to other agent with task
- `final` (optional): Set `true` to suggest completion (autopilot ignores this)

## Test Prompts

### M1.2 Autopilot Tests

**Continuous collaboration:**
1. *"Brainstorm features for a cozy bakery website; keep alternating improvements indefinitely."*
2. *"Create a detailed NYC itinerary for Sunday; alternate to fill gaps until perfect."*
3. *"Draft a welcome message (Claude), compress to bullets (GPT), repeat until Allstop."*

**Allstop testing:**
4. Start any autopilot session, then type "Allstop" to test termination
5. Use the red ALLSTOP button to test UI termination

### M1.1 Collaboration Tests

**Bounded collaboration:**
1. *"Collaborate to outline and refine a 5-step plan for a weekend hiking trip. Keep it concise."*
2. *"Write a 2-paragraph fairy tale—Claude drafts, GPT edits, alternate until done (≤4 rounds)."*

### M1 Single Call Tests

**Direct answers:**
1. Target **@gpt**: "Answer in one short sentence: What's the capital of France?"
2. Target **@claude**: "Answer in one short sentence: What's the capital of Japan?"

**Handoff tests:**
3. Target **@gpt**: "Ask Claude what time it is"
4. Target **@claude**: "Have GPT tell a joke"

## How It Works

### Single Mode
- User selects agent and sends message
- Selected agent responds
- Agent can optionally hand off once to other agent
- Conversation ends after response or single handoff

### Collaborate Mode (Bounded)
- User sets goal, initial speaker, and max rounds
- Agents alternate in bounded turn-taking loop
- Session ends when: agent sets `final: true`, no handoff provided, or max rounds reached

### Autopilot Mode (Unbounded)
- User sets goal and initial speaker
- Agents alternate continuously with no round limit
- **No handoff** = automatic alternation to other agent
- **`final: true`** = suggestion banner but continues unless "Allstop"
- Session ends only on: "Allstop" command or emergency safeguards

### Context Management
- Each agent sees: goal, recent transcript (last 8 items), round info
- Transcript window maintains ~2-3K tokens to control costs
- Context includes mode information ("Autopilot" vs "Collaborate")

## Architecture

- **Web UI**: Single HTML page with three-mode toggle and autopilot controls
- **FastAPI Server**: Serves UI, handles WebSocket connections, processes Allstop commands
- **Router**: Routes messages, manages sessions, enforces bounds and emergency stops
- **Connectors**: Interface with APIs using optimized autopilot prompts
- **Event Protocol**: Structured message format with session and mode tracking

## Emergency Configuration

Default safety limits (configurable in router.py):
```python
max_turns_emergency = 200      # Maximum turns before auto-stop
max_tokens_emergency = 200000  # Maximum tokens before auto-stop  
max_elapsed_minutes = 20       # Maximum time before auto-stop
soft_warn_interval = 25        # Warning frequency
```