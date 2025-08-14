import os
from typing import Dict, Any
from pydantic import BaseModel

class SettingsManager:
    """Manages application settings and environment variables."""
    
    def __init__(self):
        self.settings = self._load_settings()
    
    def _load_settings(self) -> Dict[str, Any]:
        """Load settings from environment variables."""
        return {
            "openai_api_key": os.getenv("OPENAI_API_KEY"),
            "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY"),
            "port": int(os.getenv("PORT", "8000")),
            "bind_host": os.getenv("BIND_HOST", "127.0.0.1"),
            "log_level": os.getenv("LOG_LEVEL", "INFO")
        }
    
    def get(self, key: str, default=None):
        """Get a setting value."""
        return self.settings.get(key, default)
    
    def get_openai_key(self) -> str:
        """Get OpenAI API key."""
        key = self.get("openai_api_key")
        if not key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        return key
    
    def get_anthropic_key(self) -> str:
        """Get Anthropic API key."""
        key = self.get("anthropic_api_key")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        return key
    
    def get_port(self) -> int:
        """Get server port."""
        return self.get("port", 8000)
    
    def get_host(self) -> str:
        """Get bind host."""
        return self.get("bind_host", "127.0.0.1")