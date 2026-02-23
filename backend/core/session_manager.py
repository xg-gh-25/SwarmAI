"""Session management for chat conversations."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from database import db


@dataclass
class SessionInfo:
    """Information about a conversation session."""

    session_id: str
    agent_id: str
    title: str = "New Chat"
    user_id: Optional[str] = None
    work_dir: Optional[str] = None  # Working directory for session continuity
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.session_id,
            "agent_id": self.agent_id,
            "title": self.title,
            "user_id": self.user_id,
            "work_dir": self.work_dir,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionInfo:
        """Create from dictionary."""
        return cls(
            session_id=data.get("id", data.get("session_id", "")),
            agent_id=data.get("agent_id", ""),
            title=data.get("title", "New Chat"),
            user_id=data.get("user_id"),
            work_dir=data.get("work_dir"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            last_accessed=data.get("last_accessed", datetime.now().isoformat()),
        )


class SessionManager:
    """Manages conversation sessions with database persistence."""

    def __init__(self):
        # In-memory cache for performance
        self._cache: dict[str, SessionInfo] = {}

    async def store_session(
        self,
        session_id: str,
        agent_id: str,
        title: str = "New Chat",
        user_id: Optional[str] = None,
        work_dir: Optional[str] = None,
    ) -> SessionInfo:
        """Store session information in database."""
        now = datetime.now().isoformat()

        # Check if session exists
        existing = await db.sessions.get(session_id)
        if existing:
            # Update last_accessed
            updates = {"last_accessed": now}
            if title and title != "New Chat":
                updates["title"] = title
            # Update work_dir if provided (don't overwrite with None)
            if work_dir is not None:
                updates["work_dir"] = work_dir
            await db.sessions.update(session_id, updates)
            existing.update(updates)
            session_info = SessionInfo.from_dict(existing)
        else:
            # Create new session
            session_data = {
                "id": session_id,
                "agent_id": agent_id,
                "title": title,
                "user_id": user_id,
                "work_dir": work_dir,
                "created_at": now,
                "last_accessed": now,
            }
            await db.sessions.put(session_data)
            session_info = SessionInfo.from_dict(session_data)

        # Update cache
        self._cache[session_id] = session_info
        return session_info

    async def session_exists(self, session_id: str) -> bool:
        """Check if session exists."""
        if session_id in self._cache:
            return True
        existing = await db.sessions.get(session_id)
        return existing is not None

    async def get_session(self, session_id: str) -> Optional[SessionInfo]:
        """Get session information."""
        # Check cache first
        if session_id in self._cache:
            return self._cache[session_id]

        # Fetch from database
        session_data = await db.sessions.get(session_id)
        if session_data:
            session_info = SessionInfo.from_dict(session_data)
            self._cache[session_id] = session_info
            return session_info
        return None

    async def delete_session(self, session_id: str) -> bool:
        """Delete session."""
        deleted = await db.sessions.delete(session_id)
        if session_id in self._cache:
            del self._cache[session_id]
        return deleted

    async def list_sessions(
        self,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> list[SessionInfo]:
        """List all sessions, optionally filtered by agent_id or user_id."""
        # Fetch from database with user_id filter if provided
        sessions_data = await db.sessions.list(user_id=user_id)

        # Convert to SessionInfo objects
        sessions = [SessionInfo.from_dict(s) for s in sessions_data]

        # Filter by agent_id if provided
        if agent_id:
            sessions = [s for s in sessions if s.agent_id == agent_id]

        # Sort by last_accessed descending
        return sorted(sessions, key=lambda s: s.last_accessed, reverse=True)

    async def update_title(self, session_id: str, title: str) -> bool:
        """Update session title."""
        updated = await db.sessions.update(session_id, {"title": title})
        if updated:
            if session_id in self._cache:
                self._cache[session_id].title = title
            return True
        return False


# Global instance
session_manager = SessionManager()
