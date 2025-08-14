import asyncio
import json
import logging
import time
import uuid
import re
from typing import Dict, Any, Callable, Optional, Set

logger = logging.getLogger(__name__)

class Router:
    """Routes messages between agents and manages collaboration sessions."""
    
    def __init__(self, settings_manager):
        self.settings_manager = settings_manager
        self.hop_counts = {}  # Track hops per conversation
        self.collaboration_sessions = {}  # Track active collaboration sessions
        self.allstop_requests: Set[str] = set()  # Track allstop requests to ignore in-flight responses
        self.call_depth: Dict[str, int] = {}  # Track recursion depth per session
        
        # Emergency safeguards
        self.max_turns_emergency = 200
        self.max_tokens_emergency = 200000
        self.max_elapsed_minutes = 20
        self.soft_warn_interval = 25
        self.max_call_depth = 50  # Prevent infinite recursion
        
        # Import connectors here to avoid circular imports
        from .connectors.openai_conn import OpenAIConnector
        from .connectors.anthropic_conn import AnthropicConnector
        
        self.connectors = {
            "gpt": OpenAIConnector(settings_manager),
            "claude": AnthropicConnector(settings_manager)
        }
    
    async def process_event(self, event: Dict[str, Any], broadcast_fn: Callable):
        """Process an incoming event and route to appropriate handler."""
        try:
            event_type = event.get("type")
            
            if event_type == "human_message":
                await self._handle_human_message(event, broadcast_fn)
            elif event_type == "agent_call":
                await self._handle_agent_call(event, broadcast_fn)
            elif event_type == "start_collaboration":
                await self._handle_start_collaboration(event, broadcast_fn)
            elif event_type == "stop_collaboration":
                await self._handle_stop_collaboration(event, broadcast_fn)
                
        except Exception as e:
            logger.error(f"Error processing event: {e}")
            error_event = {
                "type": "error",
                "text": f"Error processing message: {str(e)}",
                "ts": int(time.time())
            }
            await broadcast_fn(error_event)
    
    async def _handle_human_message(self, event: Dict[str, Any], broadcast_fn: Callable):
        """Handle a human message by routing to the specified target."""
        text = event.get("text", "")
        
        # Check for allstop command
        if self._is_allstop_command(text):
            await self._handle_allstop_from_text(broadcast_fn)
            return
        
        target = event.get("target")
        call_id = event.get("call_id", str(uuid.uuid4()))
        
        # Initialize hop count for this conversation
        self.hop_counts[call_id] = 0
        
        if target in ["gpt", "claude"]:
            # Create agent_call event
            agent_call = {
                "type": "agent_call",
                "sender": "router",
                "target": target,
                "thread": event.get("thread", "default"),
                "text": text,
                "ts": int(time.time()),
                "call_id": call_id
            }
            
            await self._handle_agent_call(agent_call, broadcast_fn)
        else:
            # Invalid target
            error_event = {
                "type": "error",
                "text": f"Invalid target: {target}. Use @gpt or @claude",
                "ts": int(time.time())
            }
            await broadcast_fn(error_event)
    
    async def _handle_agent_call(self, event: Dict[str, Any], broadcast_fn: Callable):
        """Handle an agent call by invoking the appropriate connector."""
        target = event.get("target")
        call_id = event.get("call_id", "")
        session_id = event.get("session_id")
        
        # Check if this call should be ignored due to allstop
        if session_id:
            if session_id in self.allstop_requests:
                logger.info(f"Ignoring agent call for stopped session {session_id}")
                return
            
            # Check if session is ended
            session = self.collaboration_sessions.get(session_id)
            if not session or session.get("status") == "ended":
                logger.info(f"Ignoring agent call for ended session {session_id}")
                return
                
            # Recursion depth protection
            current_depth = self.call_depth.get(session_id, 0)
            if current_depth >= self.max_call_depth:
                logger.error(f"Maximum call depth ({self.max_call_depth}) exceeded for session {session_id}")
                await self._end_collaboration_session(session_id, f"Maximum call depth exceeded", broadcast_fn)
                return
            
            self.call_depth[session_id] = current_depth + 1
        
        # Check hop limit for single mode
        if not session_id:
            current_hops = self.hop_counts.get(call_id, 0)
            if current_hops >= 1:
                logger.warning(f"Hop limit reached for call {call_id}")
                return
            self.hop_counts[call_id] = current_hops + 1
        
        if target not in self.connectors:
            error_event = {
                "type": "error",
                "text": f"Unknown agent: {target}",
                "ts": int(time.time())
            }
            await broadcast_fn(error_event)
            return
        
        # Get session context if in collaboration mode
        session_context = None
        if session_id:
            session_context = self._get_session_context(session_id)
            if not session_context:
                logger.warning(f"Session {session_id} not found")
                return
        
        # Call the connector
        connector = self.connectors[target]
        start_time = time.time()
        
        try:
            if session_context:
                response = await connector.process_collaboration_message(
                    event.get("text", ""), 
                    session_context
                )
            else:
                response = await connector.process_message(event.get("text", ""))
            
            latency_ms = int((time.time() - start_time) * 1000)
            logger.info(f"Agent {target} responded in {latency_ms}ms")
            
            # Double-check if session was stopped while processing
            if session_id:
                if session_id in self.allstop_requests:
                    logger.info(f"Discarding response for stopped session {session_id}")
                    return
                    
                session = self.collaboration_sessions.get(session_id)
                if not session or session.get("status") == "ended":
                    logger.info(f"Discarding response for ended session {session_id}")
                    return
            
            # Handle collaboration response
            if session_context:
                await self._handle_collaboration_response(
                    session_id, target, response, broadcast_fn
                )
            else:
                # Handle single mode response
                await self._handle_single_response(
                    event, target, response, broadcast_fn
                )
                
        except Exception as e:
            logger.error(f"Error calling {target}: {e}")
            error_event = {
                "type": "error",
                "text": f"Error from {target}: {str(e)}",
                "ts": int(time.time())
            }
            await broadcast_fn(error_event)
        finally:
            # Decrement call depth
            if session_id and session_id in self.call_depth:
                self.call_depth[session_id] = max(0, self.call_depth[session_id] - 1)
    
    async def _handle_single_response(self, original_event: Dict[str, Any], sender: str, response: Any, broadcast_fn: Callable):
        """Handle a response in single mode."""
        call_id = original_event.get("call_id", "")
        
        if isinstance(response, dict) and "handoff" in response:
            # Handle handoff
            handoff_data = response["handoff"]
            handoff_target = handoff_data.get("to")
            handoff_task = handoff_data.get("task")
            
            # Broadcast the handoff event
            handoff_event = {
                "type": "agent_response",
                "sender": sender,
                "target": "all",
                "thread": original_event.get("thread", "default"),
                "text": f"[Handing off to {handoff_target}: {handoff_task}]",
                "ts": int(time.time()),
                "call_id": call_id,
                "handoff": handoff_data
            }
            await broadcast_fn(handoff_event)
            
            # Create new agent call for handoff target
            if handoff_target in ["gpt", "claude"] and handoff_target != sender:
                handoff_call = {
                    "type": "agent_call",
                    "sender": "router",
                    "target": handoff_target,
                    "thread": original_event.get("thread", "default"),
                    "text": handoff_task,
                    "ts": int(time.time()),
                    "call_id": call_id
                }
                await self._handle_agent_call(handoff_call, broadcast_fn)
        else:
            # Normal text response
            response_event = {
                "type": "agent_response",
                "sender": sender,
                "target": "all",
                "thread": original_event.get("thread", "default"),
                "text": response,
                "ts": int(time.time()),
                "call_id": call_id
            }
            await broadcast_fn(response_event)
    
    async def _handle_start_collaboration(self, event: Dict[str, Any], broadcast_fn: Callable):
        """Start a new collaboration session."""
        session_id = event.get("session_id")
        goal = event.get("goal")
        initial_speaker = event.get("initial_speaker", "gpt")
        mode = event.get("mode", "collaborate")
        max_rounds = event.get("max_rounds")
        
        if not session_id or not goal:
            error_event = {
                "type": "error",
                "text": "Missing session_id or goal for collaboration",
                "ts": int(time.time())
            }
            await broadcast_fn(error_event)
            return
        
        # Create session
        session = {
            "id": session_id,
            "goal": goal,
            "mode": mode,
            "initial_speaker": initial_speaker,
            "current_speaker": initial_speaker,
            "max_rounds": max_rounds,
            "round": 1,
            "status": "active",
            "started_at": time.time(),
            "transcript": [],
            "total_tokens": 0
        }
        
        self.collaboration_sessions[session_id] = session
        
        # Remove from allstop requests if it was there
        self.allstop_requests.discard(session_id)
        
        # Broadcast start event
        start_event = {
            "type": "collaboration_started",
            "session_id": session_id,
            "goal": goal,
            "mode": mode,
            "initial_speaker": initial_speaker,
            "ts": int(time.time())
        }
        await broadcast_fn(start_event)
        
        # Start the collaboration by calling the initial speaker
        agent_call = {
            "type": "agent_call",
            "sender": "router",
            "target": initial_speaker,
            "session_id": session_id,
            "text": goal,
            "ts": int(time.time()),
            "call_id": f"collab_{session_id}_{session['round']}"
        }
        
        await self._handle_agent_call(agent_call, broadcast_fn)
    
    async def _handle_collaboration_response(self, session_id: str, sender: str, response: Any, broadcast_fn: Callable):
        """Handle a response in collaboration mode."""
        session = self.collaboration_sessions.get(session_id)
        if not session:
            logger.warning(f"Session {session_id} not found during response handling")
            return
        
        # Safety check: prevent infinite loops
        if session["round"] > self.max_turns_emergency:
            logger.error(f"Emergency stop: too many rounds in session {session_id}")
            await self._end_collaboration_session(session_id, f"Emergency stop: {self.max_turns_emergency} rounds reached", broadcast_fn)
            return

        # Parse collaboration response
        if isinstance(response, dict):
            message = response.get("message", "")
            handoff = response.get("handoff")
            final = response.get("final", False)
        else:
            # Fallback for plain text responses
            message = str(response)
            handoff = None
            final = False
        
        # Add to transcript
        session["transcript"].append({
            "sender": sender,
            "message": message,
            "round": session["round"],
            "ts": int(time.time())
        })
        
        # Broadcast the response
        response_event = {
            "type": "agent_response",
            "sender": sender,
            "target": "all",
            "session_id": session_id,
            "round": session["round"],
            "text": message,
            "final": final,
            "ts": int(time.time())
        }
        await broadcast_fn(response_event)
        
        # Check emergency safeguards before continuing
        emergency_reason = self._check_emergency_safeguards(session)
        if emergency_reason:
            await self._end_collaboration_session(session_id, emergency_reason, broadcast_fn)
            return
        
        # Check if collaboration should continue
        should_continue = self._should_continue_collaboration(session, handoff, final)
        
        if not should_continue:
            reason = "Collaboration completed"
            if final:
                reason = "Agent indicated completion"
            elif session["mode"] == "collaborate" and session["round"] >= session.get("max_rounds", 6):
                reason = "Maximum rounds reached"
            elif not handoff and session["mode"] == "collaborate":
                reason = "No handoff provided in bounded mode"
                
            await self._end_collaboration_session(session_id, reason, broadcast_fn)
            return
        
        # Continue collaboration - increment round BEFORE next call
        session["round"] += 1
        
        # Additional safety check after increment
        if session["round"] > self.max_turns_emergency:
            logger.error(f"Round limit exceeded after increment: {session['round']}")
            await self._end_collaboration_session(session_id, f"Round limit exceeded: {session['round']}", broadcast_fn)
            return
        
        # Determine next speaker
        if handoff and handoff.get("to") in ["gpt", "claude"]:
            next_speaker = handoff["to"]
            next_task = handoff.get("task", session["goal"])
        else:
            # Auto-alternate in autopilot mode
            next_speaker = "claude" if sender == "gpt" else "gpt"
            next_task = session["goal"]
        
        session["current_speaker"] = next_speaker
        
        # Check soft warning
        if session["round"] % self.soft_warn_interval == 0:
            warning_event = {
                "type": "system_notice",
                "text": f"Collaboration has been running for {session['round']} rounds. Consider saying 'Allstop' if complete.",
                "ts": int(time.time())
            }
            await broadcast_fn(warning_event)
        
        # Final safety check before making next agent call
        if session_id in self.allstop_requests:
            logger.info(f"Session {session_id} was stopped before next agent call")
            return
            
        # Call next agent
        agent_call = {
            "type": "agent_call",
            "sender": "router",
            "target": next_speaker,
            "session_id": session_id,
            "text": next_task,
            "ts": int(time.time()),
            "call_id": f"collab_{session_id}_{session['round']}"
        }
        
        logger.info(f"Making next agent call for round {session['round']} to {next_speaker}")
        await self._handle_agent_call(agent_call, broadcast_fn)
    
    async def _handle_stop_collaboration(self, event: Dict[str, Any], broadcast_fn: Callable):
        """Stop a collaboration session."""
        session_id = event.get("session_id")
        logger.info(f"Handling stop collaboration for session: {session_id}")
        
        if not session_id:
            logger.warning("Stop collaboration event missing session_id")
            return
            
        if session_id not in self.collaboration_sessions:
            logger.warning(f"Session {session_id} not found in collaboration_sessions")
            # Still broadcast ended event for UI cleanup
            end_event = {
                "type": "collaboration_ended",
                "session_id": session_id,
                "reason": "Session not found",
                "ts": int(time.time())
            }
            await broadcast_fn(end_event)
            return
            
        await self._end_collaboration_session(session_id, "Stopped by user", broadcast_fn)
    
    async def _handle_allstop_from_text(self, broadcast_fn: Callable):
        """Handle allstop command from chat text."""
        # Find and stop all active sessions
        active_sessions = [
            session_id for session_id, session in self.collaboration_sessions.items()
            if session.get("status") == "active"
        ]
        
        if not active_sessions:
            notice_event = {
                "type": "system_notice",
                "text": "No active collaboration session to stop.",
                "ts": int(time.time())
            }
            await broadcast_fn(notice_event)
            return
        
        for session_id in active_sessions:
            await self._end_collaboration_session(session_id, "Allstop command", broadcast_fn)
    
    async def _end_collaboration_session(self, session_id: str, reason: str, broadcast_fn: Callable):
        """End a collaboration session."""
        if not session_id:
            logger.warning("Attempted to end session with no session_id")
            return
            
        logger.info(f"Ending collaboration session {session_id}, reason: {reason}")
            
        session = self.collaboration_sessions.get(session_id)
        if session:
            session["status"] = "ended"
            session["ended_at"] = time.time()
            logger.info(f"Session {session_id} marked as ended")
        else:
            logger.warning(f"Session {session_id} not found when trying to end it")
        
        # Add to allstop requests to ignore in-flight responses
        self.allstop_requests.add(session_id)
        logger.info(f"Added {session_id} to allstop requests")
        
        # Broadcast end event
        end_event = {
            "type": "collaboration_ended",
            "session_id": session_id,
            "reason": reason,
            "ts": int(time.time())
        }
        await broadcast_fn(end_event)
        logger.info(f"Broadcasted collaboration_ended event for {session_id}")
        
        # Clean up immediately and again after delay
        await self._cleanup_session_immediate(session_id)
        asyncio.create_task(self._cleanup_session_delayed(session_id))
    
    async def _cleanup_session_immediate(self, session_id: str):
        """Immediate cleanup of session data."""
        if session_id in self.collaboration_sessions:
            self.collaboration_sessions[session_id]["status"] = "ended"
        
        # Clean up call depth tracking
        self.call_depth.pop(session_id, None)
        
    async def _cleanup_session_delayed(self, session_id: str):
        """Clean up session data after a delay."""
        await asyncio.sleep(3)  # Wait 3 seconds to ignore in-flight responses
        self.allstop_requests.discard(session_id)
        self.collaboration_sessions.pop(session_id, None)
        self.call_depth.pop(session_id, None)  # Double cleanup
        logger.info(f"Session {session_id} fully cleaned up")
    
    def _get_session_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get context for a collaboration session."""
        session = self.collaboration_sessions.get(session_id)
        if not session:
            return None
        
        # Get recent transcript (last 8 items to control token usage)
        transcript = session["transcript"][-8:] if len(session["transcript"]) > 8 else session["transcript"]
        
        return {
            "goal": session["goal"],
            "round": session["round"],
            "max_rounds": session.get("max_rounds"),
            "mode": session["mode"],
            "transcript": transcript
        }
    
    def _should_continue_collaboration(self, session: Dict[str, Any], handoff: Optional[Dict], final: bool) -> bool:
        """Determine if collaboration should continue."""
        mode = session["mode"]
        
        if mode == "collaborate":
            # Bounded mode: stop if final=true, no handoff, or max rounds reached
            if final:
                return False
            if not handoff:
                return False
            if session["round"] >= session.get("max_rounds", 6):
                return False
            return True
        
        elif mode == "autopilot":
            # Unbounded mode: only stop on allstop or emergency limits
            # Ignore final flag in autopilot mode
            return True
        
        return False
    
    def _check_emergency_safeguards(self, session: Dict[str, Any]) -> Optional[str]:
        """Check if emergency safeguards should trigger."""
        # Check turn limit
        if session["round"] >= self.max_turns_emergency:
            return f"Emergency stop: {self.max_turns_emergency} turns reached"
        
        # Check time limit
        elapsed_minutes = (time.time() - session["started_at"]) / 60
        if elapsed_minutes >= self.max_elapsed_minutes:
            return f"Emergency stop: {self.max_elapsed_minutes} minutes elapsed"
        
        # Check token limit (approximate)
        if session["total_tokens"] >= self.max_tokens_emergency:
            return f"Emergency stop: {self.max_tokens_emergency} tokens reached"
        
        return None
    
    def _is_allstop_command(self, text: str) -> bool:
        """Check if text contains an allstop command."""
        if not text:
            return False
        
        # Normalize text: lowercase, remove punctuation and extra spaces
        normalized = re.sub(r'[^\w\s]', '', text.lower())
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        # Check for various forms of allstop
        allstop_patterns = [
            'allstop',
            'all stop',
            'stop all',
            'stop everything',
            'halt',
            'emergency stop'
        ]
        
        return any(pattern in normalized for pattern in allstop_patterns)