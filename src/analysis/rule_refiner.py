"""LLM-based rule refinement, aggregation, and rewriting."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from collections import defaultdict

import httpx
from dotenv import load_dotenv

from src.db.database import Database

load_dotenv(Path(__file__).parent.parent.parent / ".env")


@dataclass
class RefinedRule:
    """A refined, aggregated rule ready for review."""

    rule_text: str  # The refined instruction
    original_messages: list[str]  # Original user messages that led to this
    file_types: list[str]
    project_scope: str | None  # None = global, else project path
    confidence: float
    occurrence_count: int
    category: str  # tool, workflow, style, etc.


REFINEMENT_PROMPT = """You are refining user feedback from AI coding conversations into clear, actionable rules.

Your job is to:
1. Take raw user messages (which may be task requests, corrections, or preferences)
2. Identify the underlying generalizable instruction
3. Rewrite it as a clear rule that an AI assistant should follow

Guidelines for writing rules:
- Use imperative mood: "Use X" not "You should use X" or "Please use X"
- Be specific but generalizable: "Use uv for Python package management" not "use uv"
- Include context when relevant: "When working with .qmd files, render before committing"
- Preserve negations clearly: "Never use snap packages" not "use snap packages"
- Skip task-specific content: "install package X" → rule about package manager preference
- Combine similar messages into one comprehensive rule

Respond with a JSON object:
{
    "rule": "The clear, actionable instruction",
    "category": "tool|workflow|style|documentation|testing|software_preferences",
    "file_types": [".py", ".qmd"],  // or [] for all
    "is_global": true/false,  // false if only applies to this project
    "confidence": 0.0-1.0,
    "reasoning": "Why this rule was extracted"
}

If the messages don't contain a generalizable rule, respond:
{"rule": null, "reasoning": "explanation"}
"""


def call_anthropic(prompt: str) -> str:
    """Call Anthropic API."""
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
            "max_tokens": 512,
            "system": REFINEMENT_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"]


def get_project_name(project_path: str) -> str:
    """Extract meaningful project name from path."""
    if not project_path:
        return "unknown"

    parts = project_path.rstrip("/").split("/")

    # Skip common non-project directories
    skip = {"home", "projects", "dev", "src", "b", "Downloads"}

    # Find the most specific meaningful part
    for part in reversed(parts):
        if part and part not in skip:
            return part

    return parts[-1] if parts else "unknown"


def group_by_similarity(corrections: list[dict]) -> list[list[dict]]:
    """Group similar corrections together.

    Simple approach: group by project + extracted rule similarity.
    Future: use embeddings for semantic similarity.
    """
    # Group by project first
    by_project: dict[str, list[dict]] = defaultdict(list)
    for c in corrections:
        project = get_project_name(c.get("project_path", ""))
        by_project[project].append(c)

    groups = []

    for project, project_corrections in by_project.items():
        # Within each project, group by rough text similarity
        # For now, each correction is its own group
        # TODO: implement semantic clustering with embeddings
        for c in project_corrections:
            groups.append([c])

    return groups


def refine_rule_group(
    corrections: list[dict],
    project_path: str | None,
) -> RefinedRule | None:
    """Use LLM to refine a group of similar corrections into one rule."""
    if not corrections:
        return None

    # Build prompt with all messages in the group
    messages = []
    for c in corrections:
        msg = c.get("user_message", c.get("extracted_rule", ""))
        if msg:
            messages.append(msg)

    if not messages:
        return None

    project_name = get_project_name(project_path) if project_path else "global"

    prompt = f"""Project: {project_name}

User messages to analyze:
{chr(10).join(f'- "{m[:300]}"' for m in messages)}

Extract the generalizable rule from these messages."""

    try:
        response = call_anthropic(prompt)

        # Parse JSON response
        # Handle potential markdown code blocks
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            response = response.split("```")[1].split("```")[0]

        data = json.loads(response.strip())

        if not data.get("rule"):
            return None

        return RefinedRule(
            rule_text=data["rule"],
            original_messages=messages,
            file_types=data.get("file_types", []),
            project_scope=None if data.get("is_global", True) else project_path,
            confidence=data.get("confidence", 0.7),
            occurrence_count=len(corrections),
            category=data.get("category", "workflow"),
        )

    except Exception as e:
        print(f"Error refining rule: {e}")
        return None


def refine_corrections(db: Database) -> list[RefinedRule]:
    """Refine all unprocessed corrections into rules.

    Returns list of refined rules ready for review.
    """
    # Get corrections with their context
    corrections = db.fetchall(
        """
        SELECT c.id, c.extracted_rule, c.correction_type, c.confidence,
               m.content as user_message, conv.project_path
        FROM corrections c
        JOIN messages m ON c.message_id = m.id
        JOIN conversations conv ON m.conversation_id = conv.id
        WHERE c.reviewed = 0
        ORDER BY conv.project_path, c.confidence DESC
        """
    )

    if not corrections:
        return []

    # Convert to dicts
    correction_dicts = [
        {
            "id": c[0],
            "extracted_rule": c[1],
            "correction_type": c[2],
            "confidence": c[3],
            "user_message": c[4],
            "project_path": c[5],
        }
        for c in corrections
    ]

    # Group similar corrections
    groups = group_by_similarity(correction_dicts)

    # Refine each group
    refined_rules = []
    for group in groups:
        if not group:
            continue

        project_path = group[0].get("project_path")
        rule = refine_rule_group(group, project_path)
        if rule:
            refined_rules.append(rule)

    return refined_rules


def save_refined_rules(db: Database, rules: list[RefinedRule]) -> int:
    """Save refined rules to the review queue.

    Returns count of rules saved.
    """
    from datetime import datetime
    import uuid

    count = 0
    now = datetime.utcnow().isoformat()

    for rule in rules:
        review_id = str(uuid.uuid4())[:16]

        # Determine project scope display
        project_scope = None
        if rule.project_scope:
            project_scope = get_project_name(rule.project_scope)

        db.execute(
            """
            INSERT OR IGNORE INTO review_queue
            (id, rule_type, proposed_rule, file_types, project_scope, confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                review_id,
                rule.category,
                rule.rule_text,
                json.dumps(rule.file_types) if rule.file_types else None,
                project_scope,
                rule.confidence,
                now,
            ),
        )

        # Add evidence from original messages
        for msg in rule.original_messages[:5]:  # Keep top 5
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
                    msg[:500],  # Truncate long messages
                    rule.category,
                ),
            )

        count += 1

    return count


def refine_and_save(db: Database) -> tuple[int, int]:
    """Run the full refinement pipeline.

    Returns (rules_refined, rules_saved).
    """
    rules = refine_corrections(db)
    saved = save_refined_rules(db, rules) if rules else 0
    return len(rules), saved
