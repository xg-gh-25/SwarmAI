#!/usr/bin/env python3
"""
Manage the Pollinate topic backlog.

Usage:
    python topic_backlog.py add --message "DeepSeek V4 analysis" --source signal --score 4.1
    python topic_backlog.py list
    python topic_backlog.py pick tp_001
    python topic_backlog.py done tp_001
    python topic_backlog.py decline tp_001
    python topic_backlog.py decay
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Dict, Any


DEFAULT_BACKLOG_PATH = os.path.expanduser('~/.swarm-ai/pollinate-backlog.json')


def load_backlog(path: str) -> List[Dict[str, Any]]:
    """Load the topic backlog from JSON file."""
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_backlog(path: str, backlog: List[Dict[str, Any]]) -> None:
    """Save the topic backlog to JSON file."""
    # Create directory if it doesn't exist
    backlog_dir = os.path.dirname(path)
    if backlog_dir and not os.path.exists(backlog_dir):
        os.makedirs(backlog_dir, exist_ok=True)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(backlog, f, indent=2, ensure_ascii=False)


def generate_topic_id(backlog: List[Dict[str, Any]]) -> str:
    """Generate a new topic ID."""
    # Find highest existing ID number
    max_num = 0
    for topic in backlog:
        topic_id = topic.get('id', '')
        if topic_id.startswith('tp_'):
            try:
                num = int(topic_id[3:])
                max_num = max(max_num, num)
            except ValueError:
                continue
    return f"tp_{max_num + 1:03d}"


def add_topic(backlog: List[Dict[str, Any]], message: str, source: str, score: float,
              audience: str = None, suggested_formats: List[str] = None, urgency: str = 'medium') -> Dict[str, Any]:
    """Add a new topic to the backlog."""
    topic_id = generate_topic_id(backlog)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    topic = {
        'id': topic_id,
        'message': message,
        'source': source,
        'score': score,
        'urgency': urgency,
        'status': 'proposed',
        'created': now,
        'updated': now,
    }

    if audience:
        topic['audience'] = audience
    if suggested_formats:
        topic['suggested_formats'] = suggested_formats

    backlog.append(topic)
    return topic


def list_topics(backlog: List[Dict[str, Any]], status: str = None) -> None:
    """List topics in the backlog."""
    if not backlog:
        print("Backlog is empty.")
        return

    # Filter by status if specified
    topics = backlog
    if status:
        topics = [t for t in backlog if t.get('status') == status]
        if not topics:
            print(f"No topics with status '{status}'.")
            return

    # Sort by score (descending) then by created date (descending)
    topics = sorted(topics, key=lambda t: (-t.get('score', 0), t.get('created', '')), reverse=False)

    print(f"\nTopic Backlog ({len(topics)} topics):\n")
    for topic in topics:
        status_icon = {
            'proposed': '📋',
            'in_progress': '🔄',
            'done': '✅',
            'declined': '❌',
        }.get(topic.get('status', 'proposed'), '❓')

        print(f"{status_icon} {topic['id']} | Score: {topic.get('score', 0):.1f} | {topic.get('status', 'proposed')}")
        print(f"   {topic['message']}")
        print(f"   Source: {topic.get('source', 'unknown')} | Urgency: {topic.get('urgency', 'medium')} | Created: {topic.get('created', 'unknown')}")
        if 'audience' in topic:
            print(f"   Audience: {topic['audience']}")
        if 'suggested_formats' in topic:
            print(f"   Formats: {', '.join(topic['suggested_formats'])}")
        print()


def find_topic(backlog: List[Dict[str, Any]], topic_id: str) -> Dict[str, Any]:
    """Find a topic by ID."""
    for topic in backlog:
        if topic['id'] == topic_id:
            return topic
    return None


def update_topic_status(backlog: List[Dict[str, Any]], topic_id: str, new_status: str) -> bool:
    """Update the status of a topic."""
    topic = find_topic(backlog, topic_id)
    if not topic:
        print(f"Error: Topic '{topic_id}' not found.")
        return False

    topic['status'] = new_status
    topic['updated'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    print(f"Topic {topic_id} status updated to: {new_status}")
    return True


def decay_scores(backlog: List[Dict[str, Any]], decay_rate: float = 0.1) -> int:
    """Decay timeliness scores for old topics."""
    now = datetime.now(timezone.utc)
    decayed_count = 0

    for topic in backlog:
        if topic.get('status') not in ['proposed', 'in_progress']:
            continue

        # Calculate days since creation
        created_str = topic.get('created', '')
        try:
            created_date = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
            # Ensure timezone-aware for comparison with now (UTC)
            if created_date.tzinfo is None:
                created_date = created_date.replace(tzinfo=timezone.utc)
            days_old = (now - created_date).days
        except (ValueError, AttributeError):
            continue

        # Decay score based on age (weekly decay)
        if days_old > 7:
            weeks_old = days_old // 7
            decay_factor = (1 - decay_rate) ** weeks_old
            old_score = topic.get('score', 0)
            new_score = max(0.0, old_score * decay_factor)

            if new_score != old_score:
                topic['score'] = round(new_score, 1)
                topic['updated'] = now.strftime('%Y-%m-%d')
                decayed_count += 1
                print(f"  {topic['id']}: {old_score:.1f} -> {new_score:.1f} (age: {days_old} days)")

    return decayed_count


def build_parser():
    parser = argparse.ArgumentParser(
        description='Manage the Pollinate topic backlog',
        epilog='Backlog stored at ~/.swarm-ai/pollinate-backlog.json'
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # add command
    add_parser = subparsers.add_parser('add', help='Add a new topic to the backlog')
    add_parser.add_argument('--message', required=True, help='Topic message/description')
    add_parser.add_argument('--source', required=True,
                           choices=['signal', 'build_log', 'memory', 'user', 'calendar'],
                           help='Topic source')
    add_parser.add_argument('--score', type=float, required=True, help='Topic score (0-5)')
    add_parser.add_argument('--audience', default=None, help='Target audience (optional)')
    add_parser.add_argument('--formats', default=None, help='Suggested formats, comma-separated (optional)')
    add_parser.add_argument('--urgency', default='medium', choices=['low', 'medium', 'high'],
                           help='Urgency level (default: medium)')

    # list command
    list_parser = subparsers.add_parser('list', help='List topics in the backlog')
    list_parser.add_argument('--status', default=None,
                            choices=['proposed', 'in_progress', 'done', 'declined'],
                            help='Filter by status (optional)')

    # pick command
    pick_parser = subparsers.add_parser('pick', help='Mark a topic as in_progress')
    pick_parser.add_argument('topic_id', help='Topic ID (e.g., tp_001)')

    # done command
    done_parser = subparsers.add_parser('done', help='Mark a topic as done')
    done_parser.add_argument('topic_id', help='Topic ID (e.g., tp_001)')

    # decline command
    decline_parser = subparsers.add_parser('decline', help='Mark a topic as declined')
    decline_parser.add_argument('topic_id', help='Topic ID (e.g., tp_001)')

    # decay command
    decay_parser = subparsers.add_parser('decay', help='Decay timeliness scores for old topics')
    decay_parser.add_argument('--rate', type=float, default=0.1,
                             help='Weekly decay rate (default: 0.1 = 10%% per week)')

    parser.add_argument('--backlog-path', default=DEFAULT_BACKLOG_PATH,
                       help=f'Path to backlog file (default: {DEFAULT_BACKLOG_PATH})')

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    backlog_path = args.backlog_path
    backlog = load_backlog(backlog_path)

    if args.command == 'add':
        suggested_formats = args.formats.split(',') if args.formats else None
        topic = add_topic(
            backlog=backlog,
            message=args.message,
            source=args.source,
            score=args.score,
            audience=args.audience,
            suggested_formats=suggested_formats,
            urgency=args.urgency
        )
        save_backlog(backlog_path, backlog)
        print(f"Added topic: {topic['id']} (score: {topic['score']:.1f})")

    elif args.command == 'list':
        list_topics(backlog, status=args.status)

    elif args.command == 'pick':
        if update_topic_status(backlog, args.topic_id, 'in_progress'):
            save_backlog(backlog_path, backlog)

    elif args.command == 'done':
        if update_topic_status(backlog, args.topic_id, 'done'):
            save_backlog(backlog_path, backlog)

    elif args.command == 'decline':
        if update_topic_status(backlog, args.topic_id, 'declined'):
            save_backlog(backlog_path, backlog)

    elif args.command == 'decay':
        print(f"Decaying scores (rate: {args.rate:.1%} per week)...")
        decayed_count = decay_scores(backlog, decay_rate=args.rate)
        if decayed_count > 0:
            save_backlog(backlog_path, backlog)
            print(f"\nDecayed {decayed_count} topic(s).")
        else:
            print("No topics needed decay.")


if __name__ == '__main__':
    main()
