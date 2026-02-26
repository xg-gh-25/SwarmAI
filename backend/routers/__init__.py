"""API routers for Agent Platform."""
from .agents import router as agents_router
from .skills import router as skills_router
from .mcp import router as mcp_router
from .chat import router as chat_router
from .chat import chat_threads_router as chat_threads_router
from .auth import router as auth_router
from .workspace import router as workspace_router
from .settings import router as settings_router
from .plugins import router as plugins_router
from .tasks import router as tasks_router
from .channels import router as channels_router
from .system import router as system_router
from .todos import router as todos_router
from .sections import router as sections_router
from .plan_items import router as plan_items_router
from .communications import router as communications_router
from .artifacts import router as artifacts_router
from .reflections import router as reflections_router
from .search import router as search_router
from .workspace_config import router as workspace_config_router
from .workspace_api import router as workspace_api_router
from .projects import router as projects_router
from .context import router as context_router
from .tscc import tscc_router as tscc_router

__all__ = [
    "agents_router",
    "skills_router",
    "mcp_router",
    "chat_router",
    "chat_threads_router",
    "auth_router",
    "workspace_router",
    "settings_router",
    "plugins_router",
    "tasks_router",
    "channels_router",
    "system_router",
    "todos_router",
    "sections_router",
    "plan_items_router",
    "communications_router",
    "artifacts_router",
    "reflections_router",
    "search_router",
    "workspace_config_router",
    "workspace_api_router",
    "projects_router",
    "context_router",
    "tscc_router",
]
