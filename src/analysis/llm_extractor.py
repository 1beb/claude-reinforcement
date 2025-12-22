"""LLM-based preference and correction extraction."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import httpx
from dotenv import load_dotenv

from src.db.database import Database

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent / ".env")


@dataclass
class ExtractedPreference:
    """A preference extracted by the LLM."""

    message_id: str
    preference_type: str  # 'correction' | 'tool' | 'workflow' | 'documentation' | 'style' | 'none'
    preference_text: str | None  # The actual preference/rule to apply
    file_types: list[str]  # File types this applies to, if any
    project_types: list[str]  # Project types this applies to, if any
    confidence: float
    reasoning: str


# N-shot examples for the LLM
EXAMPLES = """
Example 1:
User message: "For python projects you should always use uv."
Analysis: {"type": "tool", "preference": "For Python projects, always use uv instead of pip or venv", "file_types": [".py"], "project_types": ["python"], "confidence": 0.95, "reasoning": "Direct instruction about tool preference for Python projects"}

Example 2:
User message: "Please also update the readme with these changes"
Analysis: {"type": "documentation", "preference": "Update the README when making significant changes", "file_types": [], "project_types": [], "confidence": 0.85, "reasoning": "Request to maintain documentation"}

Example 3:
User message: "No, use |> not %>% for pipes in R"
Analysis: {"type": "correction", "preference": "Use |> (native pipe) instead of %>% (magrittr pipe) in R code", "file_types": [".R", ".qmd"], "project_types": ["r-package", "quarto"], "confidence": 0.95, "reasoning": "Direct correction about R pipe syntax preference"}

Example 4:
User message: "Thanks, that looks good!"
Analysis: {"type": "none", "preference": null, "file_types": [], "project_types": [], "confidence": 1.0, "reasoning": "Positive feedback, not a preference"}

Example 5:
User message: "Can you make the commit message shorter?"
Analysis: {"type": "style", "preference": "Keep commit messages short and concise", "file_types": [], "project_types": [], "confidence": 0.8, "reasoning": "Style preference for commit messages"}

Example 6:
User message: "Here's the error log: Error: ENOENT: no such file..."
Analysis: {"type": "none", "preference": null, "file_types": [], "project_types": [], "confidence": 1.0, "reasoning": "Pasted error log, not a preference"}

Example 7:
User message: "Don't use emojis in markdown files"
Analysis: {"type": "style", "preference": "Do not use emojis in markdown files", "file_types": [".md", ".qmd"], "project_types": [], "confidence": 0.9, "reasoning": "Direct instruction about markdown formatting"}

Example 8:
User message: "Make sure to run the tests before committing"
Analysis: {"type": "workflow", "preference": "Run tests before committing changes", "file_types": [], "project_types": [], "confidence": 0.85, "reasoning": "Workflow instruction about testing"}

Example 9:
User message: "y"
Analysis: {"type": "none", "preference": null, "file_types": [], "project_types": [], "confidence": 1.0, "reasoning": "Single character confirmation, not a preference"}

Example 10:
User message: "When working with QMD files, always render at the end to check for errors"
Analysis: {"type": "workflow", "preference": "When working with QMD files, always render the document at the end to check for errors", "file_types": [".qmd"], "project_types": ["quarto"], "confidence": 0.9, "reasoning": "File-specific workflow instruction"}
"""

SYSTEM_PROMPT = f"""You are analyzing user messages from conversations with an AI coding assistant. Your job is to identify if a message expresses a preference, correction, or workflow instruction that should be remembered for future interactions.

Types of preferences:
- correction: User correcting a mistake (e.g., "No, do it this way")
- tool: Tool/library preferences (e.g., "Use uv for Python")
- workflow: Process preferences (e.g., "Always run tests first")
- documentation: Documentation habits (e.g., "Update the changelog")
- style: Code/output style preferences (e.g., "Be more concise")
- none: Not a preference (greetings, confirmations, pasted content, questions)

{EXAMPLES}

For each message, respond with a JSON object on a single line:
{{"type": "...", "preference": "..." or null, "file_types": [...], "project_types": [...], "confidence": 0.0-1.0, "reasoning": "..."}}

Rules:
- Only extract clear, actionable preferences
- Skip pasted content (logs, errors, code blocks)
- Skip simple confirmations (yes, ok, thanks)
- Skip questions asking for help
- The "preference" field should be a clear, reusable instruction
- Be conservative - when unsure, use type "none"
"""


def call_anthropic(messages: list[dict], max_tokens: int = 1024) -> str:
    """Call the Anthropic API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")

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
            "system": SYSTEM_PROMPT,
            "messages": messages,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    return data["content"][0]["text"]


def call_openai(messages: list[dict], max_tokens: int = 1024) -> str:
    """Call the OpenAI API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set")

    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def extract_preferences_batch(
    messages: list[tuple[str, str]],  # List of (message_id, content)
    provider: str = "anthropic",  # 'anthropic' or 'openai'
    batch_size: int = 20,
) -> list[ExtractedPreference]:
    """Extract preferences from a batch of messages using LLM.

    Args:
        messages: List of (message_id, content) tuples
        provider: Which LLM provider to use
        batch_size: How many messages to process per API call

    Returns:
        List of extracted preferences
    """
    call_llm = call_anthropic if provider == "anthropic" else call_openai
    results: list[ExtractedPreference] = []

    # Process in batches
    for i in range(0, len(messages), batch_size):
        batch = messages[i : i + batch_size]

        # Build the prompt
        prompt_lines = ["Analyze these user messages:\n"]
        for idx, (msg_id, content) in enumerate(batch, 1):
            # Truncate very long messages
            truncated = content[:500] + "..." if len(content) > 500 else content
            # Escape any JSON-breaking characters
            truncated = truncated.replace("\n", " ").replace('"', '\\"')
            prompt_lines.append(f"{idx}. [ID:{msg_id}] {truncated}")

        prompt_lines.append("\nRespond with one JSON object per line, in order:")
        prompt = "\n".join(prompt_lines)

        try:
            response = call_llm([{"role": "user", "content": prompt}])

            # Parse response - one JSON per line
            for line_idx, line in enumerate(response.strip().split("\n")):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue

                try:
                    data = json.loads(line)
                    msg_id = batch[line_idx][0] if line_idx < len(batch) else "unknown"

                    if data.get("type") != "none" and data.get("preference"):
                        results.append(
                            ExtractedPreference(
                                message_id=msg_id,
                                preference_type=data.get("type", "unknown"),
                                preference_text=data.get("preference"),
                                file_types=data.get("file_types", []),
                                project_types=data.get("project_types", []),
                                confidence=data.get("confidence", 0.5),
                                reasoning=data.get("reasoning", ""),
                            )
                        )
                except json.JSONDecodeError:
                    continue

        except Exception as e:
            print(f"Error processing batch: {e}")
            continue

    return results


def extract_preferences_from_db(
    db: Database,
    provider: str = "anthropic",
    batch_size: int = 20,
    limit: int | None = None,
) -> list[ExtractedPreference]:
    """Extract preferences from all user messages in the database.

    Args:
        db: Database connection
        provider: LLM provider to use
        batch_size: Messages per API call
        limit: Max messages to process (None = all)

    Returns:
        List of extracted preferences
    """
    # Get user messages that haven't been processed
    query = """
        SELECT m.id, m.content
        FROM messages m
        WHERE m.role = 'user'
        AND length(m.content) > 20
        AND length(m.content) < 2000
        ORDER BY m.timestamp DESC
    """
    if limit:
        query += f" LIMIT {limit}"

    messages = db.fetchall(query)

    # Filter out obvious non-preferences before sending to LLM
    filtered = []
    for msg_id, content in messages:
        # Skip very short messages
        if len(content.strip()) < 20:
            continue
        # Skip messages that are clearly pasted content
        if content.count("\n") > 15:
            continue
        # Skip system/tool messages
        if any(tag in content for tag in [
            "<system-reminder>",
            "<local-command-stdout>",
            "<command-name>",
            "<command-message>",
            "Caveat: The messages below were generated",
            "[Request interrupted",
        ]):
            continue
        filtered.append((msg_id, content))

    print(f"Processing {len(filtered)} messages with {provider}...")
    return extract_preferences_batch(filtered, provider, batch_size)


def save_extracted_preferences(
    db: Database,
    preferences: list[ExtractedPreference],
) -> int:
    """Save extracted preferences to the database.

    Returns count of preferences saved.
    """
    from datetime import datetime
    import uuid

    count = 0
    now = datetime.utcnow().isoformat()

    for pref in preferences:
        if not pref.preference_text:
            continue

        # Add to review queue
        review_id = str(uuid.uuid4())[:16]
        db.execute(
            """
            INSERT OR IGNORE INTO review_queue
            (id, rule_type, proposed_rule, file_types, project_scope, confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                review_id,
                pref.preference_type,
                pref.preference_text,
                json.dumps(pref.file_types) if pref.file_types else None,
                json.dumps(pref.project_types) if pref.project_types else None,
                pref.confidence,
                now,
            ),
        )

        # Add evidence
        evidence_id = str(uuid.uuid4())[:16]
        db.execute(
            """
            INSERT INTO review_evidence
            (id, review_id, trigger_message, evidence_type)
            VALUES (?, ?, ?, ?)
            """,
            (
                evidence_id,
                review_id,
                pref.reasoning,
                pref.preference_type,
            ),
        )

        count += 1

    return count
