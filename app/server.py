import os
import json
import asyncio
import logging
import time
from typing import Set, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Now import our modules after environment is loaded
from .settings_manager import SettingsManager
from .router import Router

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Initialize components
settings_manager = SettingsManager()
router = Router(settings_manager)

# Global state
active_connections: Set[WebSocket] = set()

class ConnectionManager:
    """Manages WebSocket connections and broadcasting."""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
    
    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast a message to all connected clients."""
        if not self.active_connections:
            return
        
        message_json = json.dumps(message)
        disconnected = set()
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                logger.error(f"Failed to send message to client: {e}")
                disconnected.add(connection)
        
        # Remove disconnected clients
        for conn in disconnected:
            self.active_connections.discard(conn)

# Global connection manager
manager = ConnectionManager()

@app.get("/")
async def serve_index():
    """Serve the main page."""
    return FileResponse("app/static/index.html")

@app.get("/settings")
async def serve_settings():
    """Serve the settings page."""
    return FileResponse("app/static/settings.html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication."""
    await manager.connect(websocket)
    logger.info("Client connected")
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON received: {data}")
                continue
                
            logger.info(f"Received message: {message}")
            
            # Handle different message types
            message_type = message.get("type")
            
            if message_type == "human_message":
                # Create event with timestamp and broadcast immediately
                event = {
                    "type": "human_message",
                    "sender": "you",
                    "target": message.get("target", "router"),
                    "thread": message.get("thread", "default"),
                    "text": message.get("text", ""),
                    "ts": int(time.time()),
                    "call_id": f"msg_{int(time.time())}"
                }
                
                await manager.broadcast(event)
                await router.process_event(event, manager.broadcast)
                
            elif message_type == "start_collaboration":
                # Handle collaboration start
                logger.info(f"Starting collaboration: {message}")
                await router.process_event(message, manager.broadcast)
                
            elif message_type == "stop_collaboration":
                # Handle collaboration stop with immediate acknowledgment
                session_id = message.get("session_id")
                logger.info(f"Stopping collaboration for session: {session_id}")
                
                if session_id:
                    await router.process_event(message, manager.broadcast)
                    
                    # Send immediate confirmation
                    stop_confirm = {
                        "type": "collaboration_ended",
                        "session_id": session_id,
                        "reason": "Stopped by user",
                        "ts": int(time.time())
                    }
                    await manager.broadcast(stop_confirm)
                else:
                    logger.warning("Stop collaboration message missing session_id")
                
            else:
                # Unknown message type
                logger.warning(f"Unknown message type: {message_type}")
                error_event = {
                    "type": "error",
                    "text": f"Unknown message type: {message_type}",
                    "ts": int(time.time())
                }
                await manager.broadcast(error_event)
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy", 
        "active_connections": len(manager.active_connections),
        "active_collaborations": len([
            s for s in router.collaboration_sessions.values() 
            if s.get("status") == "active"
        ]),
        "openai_key_configured": bool(settings_manager.get("openai_api_key")),
        "anthropic_key_configured": bool(settings_manager.get("anthropic_api_key"))
    }

@app.get("/api/settings")
async def get_settings():
    """Get current settings (excluding sensitive data)."""
    settings = settings_manager.settings.copy()
    # Remove sensitive keys
    settings.pop("openai_api_key", None)
    settings.pop("anthropic_api_key", None)
    return settings

@app.post("/api/settings")
async def update_settings(request: Request):
    """Update settings."""
    data = await request.json()
    
    # Update settings (in a real app, you'd validate and persist these)
    for key, value in data.items():
        if key in ["openai_api_key", "anthropic_api_key"]:
            # In production, you'd want to securely store these
            os.environ[key.upper()] = value
        
    logger.info("Settings updated")
    return {"status": "success", "message": "Settings updated"}

@app.get("/api/sessions")
async def get_active_sessions():
    """Get information about active collaboration sessions."""
    sessions = {}
    for session_id, session in router.collaboration_sessions.items():
        if session.get("status") == "active":
            sessions[session_id] = {
                "goal": session["goal"],
                "mode": session["mode"],
                "round": session["round"],
                "max_rounds": session.get("max_rounds"),
                "current_speaker": session.get("current_speaker"),
                "started_at": session["started_at"]
            }
    return sessions

@app.post("/api/stop")
async def stop_all_sessions():
    """Stop all active collaboration sessions via REST API."""
    try:
        # Find all active sessions
        active_sessions = [
            session_id for session_id, session in router.collaboration_sessions.items()
            if session.get("status") == "active"
        ]
        
        if not active_sessions:
            return {"success": False, "message": "No active sessions to stop", "stopped_sessions": []}
        
        # Stop all active sessions
        stopped_sessions = []
        for session_id in active_sessions:
            logger.info(f"Stopping session {session_id} via REST API")
            await router._end_collaboration_session(session_id, "Stopped via REST API", manager.broadcast)
            stopped_sessions.append(session_id)
        
        return {
            "success": True, 
            "message": f"Stopped {len(stopped_sessions)} session(s)",
            "stopped_sessions": stopped_sessions
        }
        
    except Exception as e:
        logger.error(f"Error in stop_all_sessions: {e}")
        return {"success": False, "message": f"Error stopping sessions: {str(e)}"}

@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    """Stop a specific collaboration session."""
    if session_id in router.collaboration_sessions:
        await router._end_collaboration_session(session_id, "Stopped via API", manager.broadcast)
        return {"status": "success", "message": f"Session {session_id} stopped"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")

@app.get("/debug/sessions")
async def debug_sessions():
    """Debug endpoint to view active sessions."""
    return {
        "collaboration_sessions": {
            session_id: {
                "status": session.get("status"),
                "goal": session.get("goal"),
                "round": session.get("round"),
                "mode": session.get("mode"),
                "started_at": session.get("started_at")
            }
            for session_id, session in router.collaboration_sessions.items()
        },
        "allstop_requests": list(router.allstop_requests)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings_manager.get_host(), port=settings_manager.get_port())