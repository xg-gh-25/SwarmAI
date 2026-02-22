"""DynamoDB database client for production storage."""
from __future__ import annotations

import aioboto3
from datetime import datetime
import time
from typing import Optional, TypeVar, Generic
from uuid import uuid4
from botocore.exceptions import ClientError

from config import settings
from database.base import BaseTable, BaseDatabase

T = TypeVar("T", bound=dict)


class DynamoDBTable(BaseTable[T], Generic[T]):
    """DynamoDB table implementation of BaseTable interface."""

    def __init__(self, table_name: str, session: aioboto3.Session):
        self.table_name = table_name
        self._session = session
        self._resource = None

    async def _get_table(self):
        """Get the DynamoDB table resource."""
        if self._resource is None:
            kwargs = {"region_name": settings.aws_region}
            if settings.aws_access_key_id:
                kwargs["aws_access_key_id"] = settings.aws_access_key_id
                kwargs["aws_secret_access_key"] = settings.aws_secret_access_key

            async with self._session.resource("dynamodb", **kwargs) as dynamodb:
                self._resource = await dynamodb.Table(self.table_name)
        return self._resource

    async def put(self, item: T) -> T:
        """Insert or update an item."""
        if "id" not in item:
            item["id"] = str(uuid4())
        if "created_at" not in item:
            item["created_at"] = datetime.now().isoformat()
        item["updated_at"] = datetime.now().isoformat()

        table = await self._get_table()
        await table.put_item(Item=item)
        return item

    async def get(self, item_id: str) -> Optional[T]:
        """Get an item by ID."""
        try:
            table = await self._get_table()
            response = await table.get_item(Key={"id": item_id})
            return response.get("Item")
        except ClientError:
            return None

    async def list(self, user_id: Optional[str] = None) -> list[T]:
        """List all items, optionally filtered by user_id."""
        table = await self._get_table()

        if user_id:
            # Use a GSI for user_id filtering if available
            try:
                response = await table.query(
                    IndexName="user_id-index",
                    KeyConditionExpression="user_id = :uid",
                    ExpressionAttributeValues={":uid": user_id}
                )
                return response.get("Items", [])
            except ClientError:
                # Fall back to scan with filter if GSI doesn't exist
                response = await table.scan(
                    FilterExpression="user_id = :uid",
                    ExpressionAttributeValues={":uid": user_id}
                )
                return response.get("Items", [])
        else:
            response = await table.scan()
            return response.get("Items", [])

    async def delete(self, item_id: str) -> bool:
        """Delete an item by ID."""
        try:
            table = await self._get_table()
            await table.delete_item(
                Key={"id": item_id},
                ConditionExpression="attribute_exists(id)"
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    async def update(self, item_id: str, updates: dict) -> Optional[T]:
        """Update an item."""
        if not updates:
            return await self.get(item_id)

        updates["updated_at"] = datetime.now().isoformat()

        # Build update expression
        update_expression_parts = []
        expression_attribute_names = {}
        expression_attribute_values = {}

        for i, (key, value) in enumerate(updates.items()):
            attr_name = f"#attr{i}"
            attr_value = f":val{i}"
            update_expression_parts.append(f"{attr_name} = {attr_value}")
            expression_attribute_names[attr_name] = key
            expression_attribute_values[attr_value] = value

        update_expression = "SET " + ", ".join(update_expression_parts)

        try:
            table = await self._get_table()
            response = await table.update_item(
                Key={"id": item_id},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values,
                ConditionExpression="attribute_exists(id)",
                ReturnValues="ALL_NEW"
            )
            return response.get("Attributes")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise


class DynamoDBMessagesTable(DynamoDBTable[T], Generic[T]):
    """Specialized DynamoDB table for messages with session_id querying support and TTL."""

    # TTL duration in seconds (7 days)
    TTL_SECONDS = 7 * 24 * 60 * 60  # 604800 seconds

    async def put(self, item: T) -> T:
        """Insert or update a message with TTL expiration (7 days)."""
        if "id" not in item:
            item["id"] = str(uuid4())
        if "created_at" not in item:
            item["created_at"] = datetime.now().isoformat()
        item["updated_at"] = datetime.now().isoformat()

        # Set TTL: expires 7 days from now (Unix epoch timestamp in seconds)
        item["expires_at"] = int(time.time()) + self.TTL_SECONDS

        table = await self._get_table()
        await table.put_item(Item=item)
        return item

    async def list_by_session(self, session_id: str) -> list[T]:
        """List all messages for a session, ordered by timestamp."""
        table = await self._get_table()

        try:
            # Try using GSI for session_id
            response = await table.query(
                IndexName="session_id-index",
                KeyConditionExpression="session_id = :sid",
                ExpressionAttributeValues={":sid": session_id},
                ScanIndexForward=True  # Ascending order by sort key
            )
            return response.get("Items", [])
        except ClientError:
            # Fall back to scan with filter if GSI doesn't exist
            response = await table.scan(
                FilterExpression="session_id = :sid",
                ExpressionAttributeValues={":sid": session_id}
            )
            # Sort by created_at
            items = response.get("Items", [])
            return sorted(items, key=lambda x: x.get("created_at", ""))

    async def delete_by_session(self, session_id: str) -> int:
        """Delete all messages for a session. Returns count of deleted items."""
        messages = await self.list_by_session(session_id)
        deleted_count = 0
        for msg in messages:
            if await self.delete(msg["id"]):
                deleted_count += 1
        return deleted_count


class DynamoDBSkillVersionsTable(DynamoDBTable[T], Generic[T]):
    """Specialized DynamoDB table for skill versions with skill_id querying support."""

    async def list_by_skill(self, skill_id: str) -> list[T]:
        """List all versions for a skill, ordered by version number descending."""
        table = await self._get_table()

        try:
            # Try using GSI for skill_id
            response = await table.query(
                IndexName="skill_id-index",
                KeyConditionExpression="skill_id = :sid",
                ExpressionAttributeValues={":sid": skill_id},
                ScanIndexForward=False  # Descending order (newest first)
            )
            return response.get("Items", [])
        except ClientError:
            # Fall back to scan with filter if GSI doesn't exist
            response = await table.scan(
                FilterExpression="skill_id = :sid",
                ExpressionAttributeValues={":sid": skill_id}
            )
            # Sort by version descending
            items = response.get("Items", [])
            return sorted(items, key=lambda x: x.get("version", 0), reverse=True)

    async def get_by_skill_and_version(self, skill_id: str, version: int) -> Optional[T]:
        """Get a specific version of a skill."""
        table = await self._get_table()

        try:
            # Try using GSI
            response = await table.query(
                IndexName="skill_id-index",
                KeyConditionExpression="skill_id = :sid",
                FilterExpression="version = :ver",
                ExpressionAttributeValues={":sid": skill_id, ":ver": version}
            )
            items = response.get("Items", [])
            return items[0] if items else None
        except ClientError:
            # Fall back to scan
            response = await table.scan(
                FilterExpression="skill_id = :sid AND version = :ver",
                ExpressionAttributeValues={":sid": skill_id, ":ver": version}
            )
            items = response.get("Items", [])
            return items[0] if items else None

    async def delete_by_skill(self, skill_id: str) -> int:
        """Delete all versions for a skill. Returns count of deleted items."""
        versions = await self.list_by_skill(skill_id)
        deleted_count = 0
        for ver in versions:
            if await self.delete(ver["id"]):
                deleted_count += 1
        return deleted_count


class DynamoDBDatabase(BaseDatabase):
    """DynamoDB database client implementing BaseDatabase interface."""

    def __init__(self):
        self._session = aioboto3.Session()
        self._agents = DynamoDBTable[dict](settings.dynamodb_agents_table, self._session)
        self._skills = DynamoDBTable[dict](settings.dynamodb_skills_table, self._session)
        self._mcp_servers = DynamoDBTable[dict](settings.dynamodb_mcp_table, self._session)
        self._sessions = DynamoDBTable[dict](settings.dynamodb_sessions_table, self._session)
        self._messages = DynamoDBMessagesTable[dict](settings.dynamodb_messages_table, self._session)
        self._users = DynamoDBTable[dict](settings.dynamodb_users_table, self._session)
        self._skill_versions = DynamoDBSkillVersionsTable[dict](settings.dynamodb_skill_versions_table, self._session)

    @property
    def agents(self) -> DynamoDBTable:
        """Get the agents table."""
        return self._agents

    @property
    def skills(self) -> DynamoDBTable:
        """Get the skills table."""
        return self._skills

    @property
    def mcp_servers(self) -> DynamoDBTable:
        """Get the MCP servers table."""
        return self._mcp_servers

    @property
    def sessions(self) -> DynamoDBTable:
        """Get the sessions table."""
        return self._sessions

    @property
    def messages(self) -> DynamoDBMessagesTable:
        """Get the messages table."""
        return self._messages

    @property
    def users(self) -> DynamoDBTable:
        """Get the users table."""
        return self._users

    @property
    def skill_versions(self) -> DynamoDBSkillVersionsTable:
        """Get the skill versions table."""
        return self._skill_versions

    async def health_check(self) -> bool:
        """Check if the database is healthy."""
        try:
            kwargs = {"region_name": settings.aws_region}
            if settings.dynamodb_endpoint:
                kwargs["endpoint_url"] = settings.dynamodb_endpoint
            if settings.aws_access_key_id:
                kwargs["aws_access_key_id"] = settings.aws_access_key_id
                kwargs["aws_secret_access_key"] = settings.aws_secret_access_key

            async with self._session.client("dynamodb", **kwargs) as client:
                await client.list_tables(Limit=1)
            return True
        except Exception:
            return False
