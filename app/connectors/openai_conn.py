import json
import logging
import asyncio
from typing import Dict, Any, Union
import openai

logger = logging.getLogger(__name__)

class OpenAIConnector:
    """Connector for OpenAI's ChatGPT API with collaboration support."""
    
    def __init__(self, settings_manager):
        self.settings_manager = settings_manager
        self.client = openai.AsyncOpenAI(
            api_key=settings_manager.get_openai_key()
        )
        
        # Basic system prompt for single mode
        self.base_system_prompt = """You are one of two assistants in a private room. Only speak when explicitly called.

If you want the other assistant to help, respond **only** with strict JSON (no extra text):
{ "handoff": { "to": "claude", "task": "<one sentence>" } }

If you are answering yourself, return normal text only (no JSON).
Never cause more than one handoff per message.
Your name is "gpt" for the handoff.to field."""

        # Collaboration system prompt
        self.collaboration_prompt = """You are ChatGPT, working collaboratively with Claude on a shared goal. 

COLLABORATION RULES:
- Respond with JSON in this exact format:
{
  "message": "Your response visible to everyone",
  "handoff": {
    "to": "claude", 
    "task": "Brief instruction for Claude"
  },
  "final": false
}

- "message" (required): Your contribution to the conversation
- "handoff" (optional): Pass control to Claude with a specific task
- "final" (optional): Set to true if you think the goal is complete

ROLE GUIDANCE:
- You excel at: structure, editing, organization, analysis, code optimization
- Keep responses concise (2-5 sentences) to maintain collaboration flow
- In autopilot mode, focus on iterative improvements
- If no handoff needed, omit the "handoff" field (Claude will take next turn automatically)

RESPOND ONLY WITH VALID JSON."""
    
    async def process_message(self, text: str) -> Union[str, Dict[str, Any]]:
        """Process a message in single mode."""
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": self.base_system_prompt},
                    {"role": "user", "content": text}
                ],
                max_tokens=1000,
                temperature=0.7
            )
            
            content = response.choices[0].message.content.strip()
            
            # Try to parse as JSON (for handoffs)
            try:
                parsed = json.loads(content)
                if "handoff" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass
            
            # Return as plain text
            return content
            
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise
    
    async def process_collaboration_message(self, text: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Process a message in collaboration mode."""
        try:
            # Build context-aware prompt
            goal = context.get("goal", "")
            round_num = context.get("round", 1)
            mode = context.get("mode", "collaborate")
            transcript = context.get("transcript", [])
            max_rounds = context.get("max_rounds")
            
            # Create conversation context
            conversation = [{"role": "system", "content": self.collaboration_prompt}]
            
            # Add goal and mode info
            mode_info = f"Mode: {mode.title()}"
            if mode == "collaborate" and max_rounds:
                mode_info += f" (max {max_rounds} rounds)"
            elif mode == "autopilot":
                mode_info += " (continues until Allstop)"
                
            user_content = f"GOAL: {goal}\n{mode_info}\nRound: {round_num}\n\nYour task: {text}"
            
            # Add recent transcript for context
            if transcript:
                transcript_text = "\n".join([
                    f"{item['sender']}: {item['message']}" 
                    for item in transcript[-4:]  # Last 4 messages
                ])
                user_content += f"\n\nRecent conversation:\n{transcript_text}\n\nNow respond to: {text}"
            
            conversation.append({"role": "user", "content": user_content})
            
            response = await self.client.chat.completions.create(
                model="gpt-4",
                messages=conversation,
                max_tokens=800,  # Shorter for collaboration
                temperature=0.7
            )
            
            content = response.choices[0].message.content.strip()
            
            # Parse JSON response
            try:
                parsed = json.loads(content)
                
                # Validate required fields
                if "message" not in parsed:
                    logger.warning("OpenAI response missing 'message' field")
                    return {
                        "message": content,
                        "final": False
                    }
                
                return parsed
                
            except json.JSONDecodeError:
                logger.warning(f"OpenAI returned non-JSON in collaboration mode: {content}")
                # Fallback to treating as message
                return {
                    "message": content,
                    "final": False
                }
            
        except Exception as e:
            logger.error(f"OpenAI API error in collaboration: {e}")
            raise