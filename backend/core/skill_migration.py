"""Migration logic for skill_ids (UUIDs) → allowed_skills (folder names).

This module provides an idempotent migration function that converts agent
records from the old database-backed skill reference model (``skill_ids``
containing UUIDs) to the new filesystem-based model (``allowed_skills``
containing folder name strings).

The migration is designed to run early during app startup, BEFORE the
``SkillManager`` is used, and is called from ``initialization_manager.py``.

Key public symbols:

- ``migrate_skill_ids_to_allowed_skills`` — Main migration coroutine

Requirements: 7.1–7.10
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


async def migrate_skill_ids_to_allowed_skills(db) -> None:
    """Migrate agent records from skill_ids (UUIDs) to allowed_skills (folder names).

    Steps:
        1. Check if migration is needed (skill_ids column exists, allowed_skills doesn't)
        2. For each agent record with skill_ids:
           a. Resolve each UUID to folder_name via skills table
           b. Write allowed_skills list
        3. Verify all agents updated
        4. Drop skills, skill_versions, workspace_skills tables

    Idempotent: no-op if allowed_skills already exists on agents table.

    Args:
        db: The SQLiteDatabase instance.
    """
    # ------------------------------------------------------------------
    # Step 0: Check if migration is needed
    # ------------------------------------------------------------------
    try:
        async with db._get_connection() as conn:
            cursor = await conn.execute("PRAGMA table_info(agents)")
            columns = await cursor.fetchall()
            column_names = {col[1] for col in columns}
    except Exception as e:
        logger.error("Failed to inspect agents table schema: %s", e)
        return

    # If allowed_skills already exists, migration was already applied — no-op
    if "allowed_skills" in column_names:
        logger.info("Migration skip: allowed_skills column already exists (idempotent no-op)")
        return

    # If skill_ids doesn't exist either, nothing to migrate
    if "skill_ids" not in column_names:
        logger.warning("Migration skip: neither skill_ids nor allowed_skills found on agents table")
        return

    logger.info("Starting migration: skill_ids (UUIDs) → allowed_skills (folder names)")

    # ------------------------------------------------------------------
    # Step 1: Build UUID → folder_name lookup from skills table
    # ------------------------------------------------------------------
    uuid_to_folder: dict[str, str] = {}
    try:
        async with db._get_connection() as conn:
            # Check if skills table exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='skills'"
            )
            if await cursor.fetchone() is None:
                logger.warning("Skills table does not exist — cannot resolve UUIDs")
            else:
                cursor = await conn.execute("SELECT id, folder_name, name FROM skills")
                rows = await cursor.fetchall()
                for row in rows:
                    skill_id = row[0]
                    folder_name = row[1]
                    skill_name = row[2]
                    # Prefer folder_name; fall back to kebab-cased name
                    if folder_name:
                        uuid_to_folder[skill_id] = folder_name
                    elif skill_name:
                        kebab = re.sub(r"[^a-zA-Z0-9]+", "-", skill_name.lower()).strip("-")
                        uuid_to_folder[skill_id] = kebab
                        logger.warning(
                            "Skill %s has no folder_name, derived '%s' from name '%s'",
                            skill_id, kebab, skill_name,
                        )
                logger.info("Built UUID→folder_name map with %d entries", len(uuid_to_folder))
    except Exception as e:
        logger.error("Failed to build UUID→folder_name map: %s", e)
        return

    # ------------------------------------------------------------------
    # Step 2: Add allowed_skills column to agents table
    # ------------------------------------------------------------------
    try:
        async with db._get_connection() as conn:
            await conn.execute(
                "ALTER TABLE agents ADD COLUMN allowed_skills TEXT DEFAULT '[]'"
            )
            await conn.commit()
            logger.info("Added allowed_skills column to agents table")
    except Exception as e:
        logger.error("Failed to add allowed_skills column: %s", e)
        return

    # ------------------------------------------------------------------
    # Step 3: Resolve skill_ids → allowed_skills for each agent
    # ------------------------------------------------------------------
    update_failures = 0
    agents_updated = 0

    try:
        async with db._get_connection() as conn:
            cursor = await conn.execute("SELECT id, skill_ids FROM agents")
            agents = await cursor.fetchall()

            for agent_row in agents:
                agent_id = agent_row[0]
                raw_skill_ids = agent_row[1]

                # Parse skill_ids JSON
                try:
                    skill_ids = json.loads(raw_skill_ids) if raw_skill_ids else []
                except (json.JSONDecodeError, TypeError):
                    skill_ids = []
                    logger.warning(
                        "Agent %s has unparseable skill_ids: %r",
                        agent_id, raw_skill_ids,
                    )

                # Resolve UUIDs to folder names
                allowed_skills = []
                for uid in skill_ids:
                    folder = uuid_to_folder.get(uid)
                    if folder:
                        allowed_skills.append(folder)
                    else:
                        logger.warning(
                            "Agent %s: skill UUID %s could not be resolved — skipping",
                            agent_id, uid,
                        )

                # Write allowed_skills
                try:
                    await conn.execute(
                        "UPDATE agents SET allowed_skills = ? WHERE id = ?",
                        (json.dumps(allowed_skills), agent_id),
                    )
                    agents_updated += 1
                except Exception as e:
                    logger.error("Failed to update agent %s: %s", agent_id, e)
                    update_failures += 1

            await conn.commit()
    except Exception as e:
        logger.error("Failed during agent skill_ids resolution: %s", e)
        return

    # ------------------------------------------------------------------
    # Step 4: Verify all agents were updated
    # ------------------------------------------------------------------
    if update_failures > 0:
        logger.error(
            "Migration ABORTED: %d agent(s) failed to update. "
            "Will NOT drop skill tables. Re-run migration on next startup.",
            update_failures,
        )
        return

    logger.info(
        "Migration verified: %d agent(s) updated successfully", agents_updated
    )

    # ------------------------------------------------------------------
    # Step 5: Drop legacy skill tables (skills, skill_versions, workspace_skills)
    # ------------------------------------------------------------------
    tables_to_drop = ["skill_versions", "workspace_skills", "skills"]
    _ALLOWED_DROP_TABLES = frozenset(tables_to_drop)
    try:
        async with db._get_connection() as conn:
            for table in tables_to_drop:
                # Safety: only drop tables from the hardcoded allowlist
                if table not in _ALLOWED_DROP_TABLES:
                    logger.error("Refusing to drop non-allowlisted table: %s", table)
                    continue
                # Check table exists before dropping
                cursor = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                if await cursor.fetchone() is not None:
                    # Safe: table name is from hardcoded allowlist, not user input
                    await conn.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608
                    logger.info("Dropped legacy table: %s", table)
                else:
                    logger.info("Table %s does not exist — nothing to drop", table)
            await conn.commit()
    except Exception as e:
        logger.error("Failed to drop legacy skill tables: %s", e)
        # Non-fatal: allowed_skills is already populated, tables are just stale

    logger.info("Migration complete: skill_ids → allowed_skills")
