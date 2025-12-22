"""Obsidian markdown generator for review queue and digests."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator
import json

from src.db.database import Database
from src.analysis.preferences import Preference, get_high_confidence_preferences
from src.config import ObsidianConfig


@dataclass
class ReviewItem:
    """An item in the review queue."""

    id: str
    rule_type: str
    proposed_rule: str
    file_types: list[str]
    project_scope: str | None
    confidence: float
    evidence: list[dict]
    status: str
    created_at: str


def format_confidence(confidence: float) -> str:
    """Format confidence as a percentage."""
    return f"{confidence * 100:.0f}%"


def format_file_types(file_types: list[str]) -> str:
    """Format file types as inline code."""
    if not file_types:
        return "all"
    return ", ".join(f"`{ft}`" for ft in file_types)


def format_evidence_snippet(evidence: dict) -> str:
    """Format a single evidence snippet as markdown."""
    project_path = evidence.get("project_path") or "unknown"
    project = project_path.split("/")[-1]
    timestamp = evidence.get("timestamp", "")

    # Parse timestamp to get date
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        date_str = dt.strftime("%b %d")
    except (ValueError, AttributeError):
        date_str = "unknown date"

    message = evidence.get("message", "")

    # Truncate long messages
    if len(message) > 300:
        message = message[:300] + "..."

    return f"""#### {project} - {date_str}
> **You:** =={message}==
"""


def generate_review_item_markdown(item: ReviewItem, index: int) -> str:
    """Generate markdown for a single review item."""
    file_types_str = format_file_types(item.file_types)
    scope = item.project_scope or "Global"

    evidence_md = "\n".join(
        format_evidence_snippet(e) for e in item.evidence[:5]  # Limit to 5 snippets
    )

    if len(item.evidence) > 5:
        evidence_md += f"\n_({len(item.evidence) - 5} more occurrences)_\n"

    return f"""## Rule {index}: {item.proposed_rule[:50]}{'...' if len(item.proposed_rule) > 50 else ''}

**Confidence:** {format_confidence(item.confidence)} | **File types:** {file_types_str} | **Scope:** {scope}

> {item.proposed_rule}

### Evidence ({len(item.evidence)} occurrences)

{evidence_md}

### Decision

- [ ] Approve as written
- [ ] Approve with edits: `___`
- [ ] Reject (reason: ___)
- [ ] Need more evidence

---

"""


def generate_pending_review_note(
    db: Database,
    min_confidence: float = 0.5,
) -> tuple[str, list[ReviewItem]]:
    """Generate the pending review note content.

    Returns (markdown_content, review_items).
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Get items from review queue
    results = db.fetchall(
        """
        SELECT rq.id, rq.rule_type, rq.proposed_rule, rq.file_types,
               rq.project_scope, rq.confidence, rq.status, rq.created_at
        FROM review_queue rq
        WHERE rq.status = 'pending' AND rq.confidence >= ?
        ORDER BY rq.confidence DESC
        """,
        (min_confidence,),
    )

    items: list[ReviewItem] = []
    for row in results:
        # Get evidence for this item
        evidence_results = db.fetchall(
            """
            SELECT conversation_id, project_path, timestamp,
                   trigger_message, evidence_type
            FROM review_evidence
            WHERE review_id = ?
            ORDER BY timestamp DESC
            """,
            (row[0],),
        )

        evidence = [
            {
                "conversation_id": e[0],
                "project_path": e[1],
                "timestamp": e[2],
                "message": e[3],
                "type": e[4],
            }
            for e in evidence_results
        ]

        file_types = json.loads(row[3]) if row[3] else []

        items.append(
            ReviewItem(
                id=row[0],
                rule_type=row[1],
                proposed_rule=row[2],
                file_types=file_types,
                project_scope=row[4],
                confidence=row[5],
                evidence=evidence,
                status=row[6],
                created_at=row[7],
            )
        )

    # Also include high-confidence preferences not yet in review queue
    preferences = get_high_confidence_preferences(db, min_confidence)
    for pref in preferences:
        # Check if already in queue
        existing = db.fetchone(
            "SELECT id FROM review_queue WHERE proposed_rule = ?",
            (pref.preference_value,),
        )
        if not existing:
            items.append(
                ReviewItem(
                    id=pref.id,
                    rule_type="preference",
                    proposed_rule=pref.preference_value,
                    file_types=[pref.file_extension] if pref.file_extension else [],
                    project_scope=None,
                    confidence=pref.confidence,
                    evidence=pref.evidence,
                    status="pending",
                    created_at=pref.first_seen,
                )
            )

    # Sort by confidence
    items.sort(key=lambda x: x.confidence, reverse=True)

    # Count categories
    high_confidence = sum(1 for i in items if i.confidence >= 0.85)
    needs_review = len(items) - high_confidence

    # Generate markdown
    content = f"""# Pending Rule Reviews - {today}

## Summary

- {len(items)} rules pending review
- {high_confidence} high confidence (auto-approve candidates)
- {needs_review} need review

---

"""

    for idx, item in enumerate(items, 1):
        content += generate_review_item_markdown(item, idx)

    return content, items


def generate_digest_note(db: Database) -> str:
    """Generate the daily digest note content."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Get stats
    total_conversations = db.fetchone(
        "SELECT COUNT(*) FROM conversations"
    )[0]

    new_conversations = db.fetchone(
        "SELECT COUNT(*) FROM conversations WHERE synced_at >= ?",
        (yesterday,),
    )[0]

    total_rules = db.fetchone(
        "SELECT COUNT(*) FROM learned_rules WHERE active = 1"
    )[0]

    pending_reviews = db.fetchone(
        "SELECT COUNT(*) FROM review_queue WHERE status = 'pending'"
    )[0]

    # Get recent auto-approved
    auto_approved = db.fetchall(
        """
        SELECT rule_text, approved_at
        FROM learned_rules
        WHERE approved_at >= ? AND source = 'auto'
        ORDER BY approved_at DESC
        LIMIT 5
        """,
        (yesterday,),
    )

    # Get top correction patterns
    top_patterns = db.fetchall(
        """
        SELECT c.correction_type, COUNT(*) as cnt
        FROM corrections c
        JOIN messages m ON c.message_id = m.id
        WHERE m.timestamp >= ?
        GROUP BY c.correction_type
        ORDER BY cnt DESC
        LIMIT 5
        """,
        (yesterday,),
    )

    auto_approved_md = ""
    if auto_approved:
        for rule, _ in auto_approved:
            auto_approved_md += f"- \"{rule[:80]}{'...' if len(rule) > 80 else ''}\"\n"
    else:
        auto_approved_md = "_None today_\n"

    patterns_md = ""
    if top_patterns:
        for pattern_type, count in top_patterns:
            patterns_md += f"- {pattern_type}: {count} occurrences\n"
    else:
        patterns_md = "_No patterns detected_\n"

    return f"""# Claude Reinforcement Digest - {today}

## Stats

- Conversations synced: {new_conversations} new / {total_conversations} total
- Active rules: {total_rules}
- Pending review: {pending_reviews}

## Auto-approved Today

{auto_approved_md}

## Top Correction Patterns

{patterns_md}

## Quick Links

- [[reviews/{today}-pending|Review pending rules]]
- [[rules/global|Global Rules]]

"""


def generate_index_note(db: Database) -> str:
    """Generate the index/dashboard note."""
    # Get counts
    total_rules = db.fetchone(
        "SELECT COUNT(*) FROM learned_rules WHERE active = 1"
    )[0]

    global_rules = db.fetchone(
        "SELECT COUNT(*) FROM learned_rules WHERE active = 1 AND project_scope IS NULL"
    )[0]

    pending = db.fetchone(
        "SELECT COUNT(*) FROM review_queue WHERE status = 'pending'"
    )[0]

    # Get file type breakdown
    file_type_counts = db.fetchall(
        """
        SELECT file_extension, COUNT(*)
        FROM file_type_preferences
        WHERE file_extension != ''
        GROUP BY file_extension
        ORDER BY COUNT(*) DESC
        LIMIT 10
        """
    )

    file_types_md = ""
    for ext, count in file_type_counts:
        file_types_md += f"- [[rules/by-type/{ext.replace('.', '')}|{ext} Rules]] ({count})\n"

    today = datetime.utcnow().strftime("%Y-%m-%d")

    return f"""# Claude Reinforcement Dashboard

## Quick Links

- [[reviews/{today}-pending|Pending Reviews]] ({pending} items)
- [[digests/{today}-digest|Today's Digest]]

## Stats

- **Total rules:** {total_rules}
- **Global:** {global_rules}
- **Pending review:** {pending}

## Browse Rules

- [[rules/global|Global Rules]]

### By File Type

{file_types_md}

## Recent Activity

_See daily digests for activity history_

"""


def write_obsidian_notes(db: Database, config: ObsidianConfig) -> dict[str, int]:
    """Write all Obsidian notes to the vault.

    Returns dict with counts of files written.
    """
    output_path = config.output_path
    output_path.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    (output_path / "reviews").mkdir(exist_ok=True)
    (output_path / "digests").mkdir(exist_ok=True)
    (output_path / "rules" / "by-type").mkdir(parents=True, exist_ok=True)
    (output_path / "rules" / "by-project").mkdir(parents=True, exist_ok=True)
    (output_path / "archive").mkdir(exist_ok=True)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    counts = {"reviews": 0, "digests": 0, "index": 0}

    # Generate pending review note
    review_content, _ = generate_pending_review_note(db)
    review_path = output_path / "reviews" / f"{today}-pending.md"
    review_path.write_text(review_content)
    counts["reviews"] = 1

    # Generate digest note
    digest_content = generate_digest_note(db)
    digest_path = output_path / "digests" / f"{today}-digest.md"
    digest_path.write_text(digest_content)
    counts["digests"] = 1

    # Generate index note
    index_content = generate_index_note(db)
    index_path = output_path / "index.md"
    index_path.write_text(index_content)
    counts["index"] = 1

    return counts
