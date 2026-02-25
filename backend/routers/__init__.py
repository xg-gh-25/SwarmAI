"""API routers for Agent Platform."""
from .agents import router as agents_router
from .skills import router as skills_router
from .mcp import router as mcp_router
from .chat import router as chat_router
from .auth import router as auth_router
from .workspace import router as workspace_router
from .settings import router as settings_router
from .plugins import router as plugins_router
from .tasks import router as tasks_router

__all__ = [
    "agents_router",
    "skills_router",
    "mcp_router",
    "chat_router",
    "auth_router",
    "workspace_router",
    "settings_router",
    "plugins_router",
    "tasks_router",
]
