"""Conversation ingestion from Claude Code JSONL files."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator
import hashlib
import uuid

from src.db.database import Database


@dataclass
class RawMessage:
    """Raw message from Claude Code JSONL."""

    uuid: str
    type: str  # 'user' | 'assistant'
    content: str
    timestamp: str
    parent_uuid: str | None
    session_id: str
    cwd: str | None = None
    git_branch: str | None = None


@dataclass
class Conversation:
    """Parsed conversation with messages."""

    id: str
    session_id: str
    project_path: str
    device_id: str
    started_at: str | None
    ended_at: str | None
    git_branch: str | None
    messages: list[RawMessage]


def parse_jsonl_file(file_path: Path) -> Iterator[dict]:
    """Parse a JSONL file and yield records."""
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def extract_messages(file_path: Path) -> list[RawMessage]:
    """Extract messages from a Claude Code conversation file."""
    messages = []

    for record in parse_jsonl_file(file_path):
        record_type = record.get("type")

        # Only process user and assistant messages
        if record_type not in ("user", "assistant"):
            continue

        # Extract message content
        message_data = record.get("message", {})
        content = ""

        # Handle different content formats
        if isinstance(message_data, dict):
            content_field = message_data.get("content", "")
            if isinstance(content_field, str):
                content = content_field
            elif isinstance(content_field, list):
                # Extract text from content blocks
                text_parts = []
                for block in content_field:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif "text" in block:
                            text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)
        elif isinstance(message_data, str):
            content = message_data

        # Skip empty messages
        if not content.strip():
            continue

        messages.append(
            RawMessage(
                uuid=record.get("uuid", str(uuid.uuid4())),
                type=record_type,
                content=content,
                timestamp=record.get("timestamp", ""),
                parent_uuid=record.get("parentUuid"),
                session_id=record.get("sessionId", ""),
                cwd=record.get("cwd"),
                git_branch=record.get("gitBranch"),
            )
        )

    return messages


def project_path_from_file(file_path: Path, claude_projects_dir: Path) -> str:
    """Extract project path from Claude projects directory structure.

    Claude stores projects with paths like:
    ~/.claude/projects/-home-b-projects-rental-app/session.jsonl

    Returns the original project path: /home/b/projects/rental-app
    """
    # Get the directory name (e.g., "-home-b-projects-rental-app")
    project_dir = file_path.parent.name

    # Convert dashes back to slashes, handling the leading dash
    if project_dir.startswith("-"):
        project_dir = project_dir[1:]  # Remove leading dash

    # Replace dashes with slashes to reconstruct path
    # This is imperfect - paths with actual dashes will be wrong
    # But it matches Claude Code's encoding scheme
    project_path = "/" + project_dir.replace("-", "/")

    return project_path


def generate_conversation_id(session_id: str, project_path: str) -> str:
    """Generate a unique conversation ID."""
    data = f"{session_id}:{project_path}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def ingest_conversation_file(
    file_path: Path,
    device_id: str,
    claude_projects_dir: Path,
) -> Conversation | None:
    """Ingest a single conversation file."""
    messages = extract_messages(file_path)

    if not messages:
        return None

    # Get project path from directory structure
    project_path = project_path_from_file(file_path, claude_projects_dir)

    # Get session ID from first message or filename
    session_id = messages[0].session_id if messages else file_path.stem

    # Get timestamps
    timestamps = [m.timestamp for m in messages if m.timestamp]
    started_at = min(timestamps) if timestamps else None
    ended_at = max(timestamps) if timestamps else None

    # Get git branch (from first message that has it)
    git_branch = None
    for msg in messages:
        if msg.git_branch:
            git_branch = msg.git_branch
            break

    return Conversation(
        id=generate_conversation_id(session_id, project_path),
        session_id=session_id,
        project_path=project_path,
        device_id=device_id,
        started_at=started_at,
        ended_at=ended_at,
        git_branch=git_branch,
        messages=messages,
    )


def discover_conversation_files(claude_projects_dir: Path) -> Iterator[Path]:
    """Discover all conversation JSONL files in Claude projects directory."""
    if not claude_projects_dir.exists():
        return

    for jsonl_file in claude_projects_dir.rglob("*.jsonl"):
        # Skip agent files (subagent conversations)
        if jsonl_file.name.startswith("agent-"):
            continue
        yield jsonl_file


def save_conversation(db: Database, conversation: Conversation) -> bool:
    """Save a conversation to the database.

    Returns True if new conversation was inserted, False if updated.
    """
    now = datetime.utcnow().isoformat()

    # Check if conversation exists
    existing = db.fetchone(
        "SELECT id FROM conversations WHERE id = ?",
        (conversation.id,)
    )

    with db.transaction() as cursor:
        if existing:
            # Update existing conversation
            cursor.execute(
                """
                UPDATE conversations
                SET ended_at = ?, synced_at = ?
                WHERE id = ?
                """,
                (conversation.ended_at, now, conversation.id),
            )
            is_new = False
        else:
            # Insert new conversation
            cursor.execute(
                """
                INSERT INTO conversations
                (id, device_id, project_path, session_id, started_at, ended_at, git_branch, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation.id,
                    conversation.device_id,
                    conversation.project_path,
                    conversation.session_id,
                    conversation.started_at,
                    conversation.ended_at,
                    conversation.git_branch,
                    now,
                ),
            )
            is_new = True

        # Upsert messages
        for msg in conversation.messages:
            cursor.execute(
                """
                INSERT OR REPLACE INTO messages
                (id, conversation_id, role, content, timestamp, parent_uuid)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.uuid,
                    conversation.id,
                    msg.type,
                    msg.content,
                    msg.timestamp,
                    msg.parent_uuid,
                ),
            )

    return is_new


def ingest_all_conversations(
    db: Database,
    claude_projects_dir: Path,
    device_id: str,
) -> tuple[int, int]:
    """Ingest all conversations from Claude projects directory.

    Returns (new_count, updated_count).
    """
    new_count = 0
    updated_count = 0

    for file_path in discover_conversation_files(claude_projects_dir):
        conversation = ingest_conversation_file(file_path, device_id, claude_projects_dir)

        if conversation:
            is_new = save_conversation(db, conversation)
            if is_new:
                new_count += 1
            else:
                updated_count += 1

    return new_count, updated_count
