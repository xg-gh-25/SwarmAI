"""Test script for chat_threads, chat_messages, and thread_summaries tables."""
import asyncio
import sys
sys.path.insert(0, '.')
from database.sqlite import SQLiteDatabase
import tempfile
import os
from uuid import uuid4


async def test_crud_operations():
    """Test CRUD operations for chat tables."""
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        db = SQLiteDatabase(db_path)
        await db.initialize()
        
        # First create required parent records
        workspace_id = 'swarmws'
        agent_id = str(uuid4())
        
        # Ensure workspace_config row exists (singleton model)
        existing = await db.workspace_config.get_config()
        if not existing:
            await db.workspace_config.put({
                'id': 'swarmws',
                'name': 'Test Workspace',
                'file_path': '/tmp/test',
                'icon': '',
                'context': 'Test context',
                'created_at': '2025-01-01T00:00:00',
                'updated_at': '2025-01-01T00:00:00',
            })
        
        # Create agent
        await db.agents.put({
            'id': agent_id,
            'name': 'Test Agent',
            'description': 'Test agent'
        })
        
        # Test chat_threads CRUD
        thread_id = str(uuid4())
        thread = await db.chat_threads.put({
            'id': thread_id,
            'workspace_id': workspace_id,
            'agent_id': agent_id,
            'mode': 'explore',
            'title': 'Test Thread'
        })
        print(f'Created chat_thread: {thread["id"]}')
        
        # Test list_by_workspace
        threads = await db.chat_threads.list_by_workspace(workspace_id)
        assert len(threads) == 1, f'Expected 1 thread, got {len(threads)}'
        print(f'list_by_workspace returned {len(threads)} thread(s)')
        
        # Test list_by_task (with no task)
        threads_by_task = await db.chat_threads.list_by_task('nonexistent')
        assert len(threads_by_task) == 0, f'Expected 0 threads, got {len(threads_by_task)}'
        print('list_by_task with nonexistent task returned 0 threads')
        
        # Test list_by_todo (with no todo)
        threads_by_todo = await db.chat_threads.list_by_todo('nonexistent')
        assert len(threads_by_todo) == 0, f'Expected 0 threads, got {len(threads_by_todo)}'
        print('list_by_todo with nonexistent todo returned 0 threads')
        
        # Test chat_messages CRUD
        message_id = str(uuid4())
        message = await db.chat_messages.put({
            'id': message_id,
            'thread_id': thread_id,
            'role': 'user',
            'content': 'Hello, world!'
        })
        print(f'Created chat_message: {message["id"]}')
        
        # Add another message
        message2_id = str(uuid4())
        await db.chat_messages.put({
            'id': message2_id,
            'thread_id': thread_id,
            'role': 'assistant',
            'content': 'Hello! How can I help you?'
        })
        
        # Test list_by_thread
        messages = await db.chat_messages.list_by_thread(thread_id)
        assert len(messages) == 2, f'Expected 2 messages, got {len(messages)}'
        print(f'list_by_thread returned {len(messages)} message(s)')
        
        # Verify messages are ordered by created_at
        assert messages[0]['role'] == 'user', 'First message should be user'
        assert messages[1]['role'] == 'assistant', 'Second message should be assistant'
        print('Messages are correctly ordered by created_at')
        
        # Test thread_summaries CRUD
        summary_id = str(uuid4())
        summary = await db.thread_summaries.put({
            'id': summary_id,
            'thread_id': thread_id,
            'summary_type': 'rolling',
            'summary_text': 'This is a test summary',
            'key_decisions': ['Decision 1'],
            'open_questions': ['Question 1']
        })
        print(f'Created thread_summary: {summary["id"]}')
        
        # Test get_by_thread
        retrieved_summary = await db.thread_summaries.get_by_thread(thread_id)
        assert retrieved_summary is not None, 'Expected summary to be found'
        print(f'get_by_thread returned summary: {retrieved_summary["summary_text"][:30]}...')
        
        # Test update summary
        updated_summary = await db.thread_summaries.put({
            'id': summary_id,
            'thread_id': thread_id,
            'summary_type': 'rolling',
            'summary_text': 'Updated summary text',
            'key_decisions': ['Decision 1', 'Decision 2'],
            'open_questions': []
        })
        assert updated_summary['summary_text'] == 'Updated summary text', 'Summary should be updated'
        print('Summary updated successfully')
        
        # Test delete_by_thread for messages
        deleted_count = await db.chat_messages.delete_by_thread(thread_id)
        assert deleted_count == 2, f'Expected 2 messages deleted, got {deleted_count}'
        print(f'delete_by_thread deleted {deleted_count} messages')
        
        # Verify messages are deleted
        messages_after = await db.chat_messages.list_by_thread(thread_id)
        assert len(messages_after) == 0, f'Expected 0 messages, got {len(messages_after)}'
        print('Messages successfully deleted')
        
        # Test delete_by_thread for summaries
        deleted_summaries = await db.thread_summaries.delete_by_thread(thread_id)
        assert deleted_summaries == 1, f'Expected 1 summary deleted, got {deleted_summaries}'
        print(f'delete_by_thread deleted {deleted_summaries} summary')
        
        # Verify summary is deleted
        summary_after = await db.thread_summaries.get_by_thread(thread_id)
        assert summary_after is None, 'Expected summary to be deleted'
        print('Summary successfully deleted')
        
        # Test thread deletion
        deleted = await db.chat_threads.delete(thread_id)
        assert deleted, 'Expected thread to be deleted'
        print('Thread successfully deleted')
        
        print('\nAll CRUD operations working correctly!')
        return True
    except Exception as e:
        print(f'ERROR: {e}')
        import traceback
        traceback.print_exc()
        return False
    finally:
        os.unlink(db_path)


if __name__ == '__main__':
    result = asyncio.run(test_crud_operations())
    sys.exit(0 if result else 1)
