"""Abstract base class for database clients."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, TypeVar, Generic

T = TypeVar("T", bound=dict)


class BaseTable(ABC, Generic[T]):
    """Abstract base class for table operations."""

    @abstractmethod
    async def put(self, item: T) -> T:
        """Insert or update an item."""
        pass

    @abstractmethod
    async def get(self, item_id: str) -> Optional[T]:
        """Get an item by ID."""
        pass

    @abstractmethod
    async def list(self, user_id: Optional[str] = None) -> list[T]:
        """List all items, optionally filtered by user_id."""
        pass

    @abstractmethod
    async def delete(self, item_id: str) -> bool:
        """Delete an item by ID."""
        pass

    @abstractmethod
    async def update(self, item_id: str, updates: dict) -> Optional[T]:
        """Update an item."""
        pass

    async def list_by_session(self, session_id: str) -> list[T]:
        """List items by session ID. Override in subclasses that support this."""
        raise NotImplementedError("list_by_session not implemented for this table")


class BaseDatabase(ABC):
    """Abstract base class for database clients."""

    @property
    @abstractmethod
    def agents(self) -> BaseTable:
        """Get the agents table."""
        pass

    @property
    @abstractmethod
    def skills(self) -> BaseTable:
        """Get the skills table."""
        pass

    @property
    @abstractmethod
    def mcp_servers(self) -> BaseTable:
        """Get the MCP servers table."""
        pass

    @property
    @abstractmethod
    def sessions(self) -> BaseTable:
        """Get the sessions table."""
        pass

    @property
    @abstractmethod
    def messages(self) -> BaseTable:
        """Get the messages table."""
        pass

    @property
    @abstractmethod
    def users(self) -> BaseTable:
        """Get the users table."""
        pass

    @property
    @abstractmethod
    def skill_versions(self) -> BaseTable:
        """Get the skill versions table."""
        pass

    @property
    @abstractmethod
    def app_settings(self) -> BaseTable:
        """Get the app settings table."""
        pass

    @property
    @abstractmethod
    def marketplaces(self) -> BaseTable:
        """Get the marketplaces table."""
        pass

    @property
    @abstractmethod
    def plugins(self) -> BaseTable:
        """Get the plugins table."""
        pass

    @property
    @abstractmethod
    def permission_requests(self) -> BaseTable:
        """Get the permission requests table (Human-in-the-Loop)."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the database is healthy."""
        pass
