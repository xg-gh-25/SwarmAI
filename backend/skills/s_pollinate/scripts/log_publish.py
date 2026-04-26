#!/usr/bin/env python3
"""
Log a publish event to the pollinate publish log.

Usage:
    python log_publish.py --topic "pollinate-v2" --channel xiaohongshu --format poster
    python log_publish.py --topic "pollinate-v2" --channel bilibili --format video --url https://...
"""
import argparse
import json
import os
from datetime import datetime, timezone


def build_parser():
    parser = argparse.ArgumentParser(
        description='Log a publish event to the pollinate publish log',
        epilog='Appends one JSON line to ~/.swarm-ai/pollinate-publish-log.jsonl'
    )
    parser.add_argument('--topic', required=True, help='Topic name or ID')
    parser.add_argument('--channel', required=True,
                        choices=['xiaohongshu', 'bilibili', 'youtube', 'douyin', 'weixin_video',
                                'gongzhonghao', 'github', 'zhihu', 'other'],
                        help='Publication channel')
    parser.add_argument('--format', required=True,
                        choices=['poster', 'video', 'narrative', 'shorts', 'readme', 'other'],
                        help='Content format')
    parser.add_argument('--url', default=None, help='Published URL (optional)')
    parser.add_argument('--log-path', default=None,
                        help='Path to log file (default: ~/.swarm-ai/pollinate-publish-log.jsonl)')
    return parser


def log_publish(topic, channel, format_type, url=None, log_path=None):
    """Append a publish event to the log file."""
    if log_path is None:
        log_path = os.path.expanduser('~/.swarm-ai/pollinate-publish-log.jsonl')

    # Create directory if it doesn't exist
    log_dir = os.path.dirname(log_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # Build log entry
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'topic': topic,
        'channel': channel,
        'format': format_type,
    }

    if url:
        entry['url'] = url

    # Append to log file
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"Logged publish: {topic} -> {channel} ({format_type})")
    if url:
        print(f"  URL: {url}")


def main():
    parser = build_parser()
    args = parser.parse_args()

    log_publish(
        topic=args.topic,
        channel=args.channel,
        format_type=args.format,
        url=args.url,
        log_path=args.log_path
    )


if __name__ == '__main__':
    main()
