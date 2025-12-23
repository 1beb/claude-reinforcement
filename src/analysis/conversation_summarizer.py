"""Conversation-level preference extraction using LLM summarization."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import httpx
from dotenv import load_dotenv

from src.db.database import Database

load_dotenv(Path(__file__).parent.parent.parent / ".env")


@dataclass
class ConversationSummary:
    """Summary of a conversation with extracted preferences."""

    conversation_id: str
    project_path: str
    project_name: str
    goal: str  # What the user was trying to accomplish
    preferences: list[dict]  # Extracted preferences with context
    corrections: list[dict]  # Things that were corrected
    tools_used: list[str]  # Tools/libraries mentioned


SUMMARY_PROMPT = """You are analyzing a coding conversation to extract user preferences and corrections.

Your job is to read the conversation and identify:
1. What was the user trying to accomplish?
2. What preferences did they express (tool choices, coding style, workflow)?
3. What corrections did they make (things Claude got wrong that were fixed)?
4. What software/tools were discussed?

Focus on extracting GENERALIZABLE preferences - things that should apply to future conversations.
Skip task-specific details that only apply to this conversation.

IMPORTANT: Write all rules as IMPERATIVE INSTRUCTIONS (commands), not statements or observations.
Use action verbs: "Use", "Run", "Always", "Never", "Ensure", "Verify", "Check", "Include", etc.

Good rule format (imperative instructions):
- "Use uv instead of pip for Python package management"
- "Run tests and linting before pushing code"
- "Always render QMD files before committing"
- "Never use emojis in documentation files"
- "Verify API endpoints are functional before declaring features complete"
- "Include loading spinners during API interactions"

Bad rule format (statements - DO NOT USE):
- "Dockerize all projects" → Instead: "Always dockerize new projects"
- "SQLite for data storage" → Instead: "Use SQLite for data storage"
- "Tests should pass" → Instead: "Ensure all tests pass before committing"

Examples to SKIP (too specific):
- "Add the login button to the header" (task-specific)
- "Fix the bug on line 42" (task-specific)
- "Use port 3000" (context-specific)

Respond with JSON:
{
    "goal": "Brief description of what the user was trying to accomplish",
    "preferences": [
        {
            "rule": "Imperative instruction starting with action verb",
            "category": "tool|workflow|style|documentation|testing|software",
            "file_types": [".py"],  // or [] for all
            "evidence": "Quote or paraphrase from the conversation",
            "confidence": 0.0-1.0
        }
    ],
    "corrections": [
        {
            "what_was_wrong": "What Claude did incorrectly",
            "correction": "What the user wanted instead",
            "rule": "Imperative instruction to prevent this in future",
            "confidence": 0.0-1.0
        }
    ],
    "tools_mentioned": ["uv", "pytest", "nextjs"]
}

If no preferences or corrections are found, return empty arrays.
"""


def call_anthropic(prompt: str, max_tokens: int = 1024) -> str:
    """Call Anthropic API with Haiku for cost efficiency."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "system": SUMMARY_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"]


def get_project_name(project_path: str) -> str:
    """Extract meaningful project name from path."""
    if not project_path:
        return "unknown"

    parts = project_path.rstrip("/").split("/")
    skip = {"home", "projects", "dev", "src", "b", "Downloads"}

    for part in reversed(parts):
        if part and part not in skip:
            return part

    return parts[-1] if parts else "unknown"


def format_conversation_lean(messages: list[tuple]) -> str:
    """Format conversation focusing on initial prompt + user feedback only.

    This is a lean approach that extracts:
    1. The initial user prompt (the goal)
    2. Subsequent user messages that look like feedback/corrections
    """
    formatted = []
    user_messages = []

    # Collect user messages only
    for msg_id, role, content, timestamp in messages:
        if role != "user":
            continue

        # Clean up system tags
        content = content.replace("<ide_opened_file>", "").replace("</ide_opened_file>", "")
        content = content.replace("<ide_selection>", "").replace("</ide_selection>", "")

        # Skip messages with system reminders
        if "<system-reminder>" in content:
            continue

        # Skip very short confirmations
        if len(content.strip()) < 10:
            continue

        user_messages.append(content)

    if not user_messages:
        return ""

    # First message is the goal
    formatted.append(f"INITIAL REQUEST:\n{user_messages[0][:800]}")

    # Look for feedback-like messages in the rest
    feedback_keywords = [
        "no", "don't", "never", "wrong", "incorrect", "instead",
        "always", "prefer", "should", "please", "actually",
        "that's not", "you should", "make sure", "remember",
    ]

    feedback_messages = []
    for msg in user_messages[1:]:
        msg_lower = msg.lower()
        if any(kw in msg_lower for kw in feedback_keywords):
            # Truncate long feedback
            feedback_messages.append(msg[:400])

    if feedback_messages:
        formatted.append("\nUSER FEEDBACK/CORRECTIONS:")
        for i, fb in enumerate(feedback_messages[:5], 1):  # Limit to 5
            formatted.append(f"{i}. {fb}")

    return "\n".join(formatted)


def summarize_conversation(
    db: Database,
    conversation_id: str,
) -> ConversationSummary | None:
    """Summarize a single conversation and extract preferences."""

    # Get conversation metadata
    conv = db.fetchone(
        "SELECT id, project_path FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    if not conv:
        return None

    project_path = conv[1]
    project_name = get_project_name(project_path)

    # Get messages
    messages = db.fetchall(
        """
        SELECT id, role, content, timestamp
        FROM messages
        WHERE conversation_id = ?
        ORDER BY timestamp
        LIMIT 50  -- Limit to keep context reasonable
        """,
        (conversation_id,),
    )

    if len(messages) < 3:
        return None  # Skip very short conversations

    # Format for LLM - lean approach with just initial prompt + feedback
    conversation_text = format_conversation_lean(messages)

    if len(conversation_text) < 100:
        return None  # Skip if too little content after filtering

    prompt = f"""Project: {project_name}
Project path: {project_path}

=== CONVERSATION ===
{conversation_text}
=== END CONVERSATION ===

Analyze this conversation and extract any user preferences or corrections."""

    try:
        response = call_anthropic(prompt)

        # Parse JSON
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            response = response.split("```")[1].split("```")[0]

        data = json.loads(response.strip())

        return ConversationSummary(
            conversation_id=conversation_id,
            project_path=project_path,
            project_name=project_name,
            goal=data.get("goal", ""),
            preferences=data.get("preferences", []),
            corrections=data.get("corrections", []),
            tools_used=data.get("tools_mentioned", []),
        )

    except Exception as e:
        print(f"Error summarizing conversation {conversation_id}: {e}")
        return None


def summarize_all_conversations(
    db: Database,
    limit: int | None = None,
) -> Iterator[ConversationSummary]:
    """Summarize all conversations and yield summaries."""

    # Get conversations ordered by recency
    query = """
        SELECT id FROM conversations
        ORDER BY ended_at DESC
    """
    if limit:
        query += f" LIMIT {limit}"

    conversations = db.fetchall(query)

    for (conv_id,) in conversations:
        summary = summarize_conversation(db, conv_id)
        if summary and (summary.preferences or summary.corrections):
            yield summary


def save_summary_preferences(
    db: Database,
    summaries: list[ConversationSummary],
) -> int:
    """Save extracted preferences from summaries to review queue."""
    from datetime import datetime
    import uuid

    count = 0
    now = datetime.utcnow().isoformat()

    for summary in summaries:
        # Save preferences
        for pref in summary.preferences:
            if not pref.get("rule"):
                continue

            review_id = str(uuid.uuid4())[:16]

            # Determine if project-specific or global
            is_global = pref.get("confidence", 0) > 0.8
            project_scope = None if is_global else summary.project_name

            db.execute(
                """
                INSERT OR IGNORE INTO review_queue
                (id, rule_type, proposed_rule, file_types, project_scope, confidence, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    review_id,
                    pref.get("category", "workflow"),
                    pref["rule"],
                    json.dumps(pref.get("file_types", [])),
                    project_scope,
                    pref.get("confidence", 0.7),
                    now,
                ),
            )

            # Add evidence
            evidence_id = str(uuid.uuid4())[:16]
            evidence = f"Goal: {summary.goal}\nEvidence: {pref.get('evidence', '')}"
            db.execute(
                """
                INSERT INTO review_evidence
                (id, review_id, conversation_id, project_path, trigger_message, evidence_type)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    review_id,
                    summary.conversation_id,
                    summary.project_path,
                    evidence[:500],
                    pref.get("category", "workflow"),
                ),
            )
            count += 1

        # Save corrections as rules
        for corr in summary.corrections:
            if not corr.get("rule"):
                continue

            review_id = str(uuid.uuid4())[:16]

            db.execute(
                """
                INSERT OR IGNORE INTO review_queue
                (id, rule_type, proposed_rule, file_types, project_scope, confidence, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    review_id,
                    "correction",
                    corr["rule"],
                    None,
                    summary.project_name,  # Corrections are usually project-specific initially
                    corr.get("confidence", 0.7),
                    now,
                ),
            )

            # Add evidence
            evidence_id = str(uuid.uuid4())[:16]
            evidence = f"What was wrong: {corr.get('what_was_wrong', '')}\nCorrection: {corr.get('correction', '')}"
            db.execute(
                """
                INSERT INTO review_evidence
                (id, review_id, conversation_id, project_path, trigger_message, evidence_type)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    review_id,
                    summary.conversation_id,
                    summary.project_path,
                    evidence[:500],
                    "correction",
                ),
            )
            count += 1

    return count
