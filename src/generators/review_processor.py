"""Process review decisions from Obsidian markdown files."""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator
import shutil

from src.db.database import Database
from src.config import ObsidianConfig


@dataclass
class ReviewDecision:
    """A parsed review decision from Obsidian."""

    rule_index: int
    proposed_rule: str
    decision: str  # 'approve' | 'approve_edited' | 'reject' | 'need_more'
    edited_rule: str | None
    reject_reason: str | None


def parse_review_file(file_path: Path) -> list[ReviewDecision]:
    """Parse a review markdown file for decisions.

    Looks for checkbox patterns:
    - [x] Approve as written
    - [x] Approve with edits: `edited rule here`
    - [x] Reject (reason: some reason)
    - [x] Need more evidence
    """
    content = file_path.read_text()
    decisions = []

    # Split by rule sections
    rule_pattern = r"## Rule (\d+):\s*(.+?)(?=\n##|\Z)"
    rule_matches = re.finditer(rule_pattern, content, re.DOTALL)

    for match in rule_matches:
        rule_index = int(match.group(1))
        section = match.group(2)

        # Extract the proposed rule (in blockquote)
        rule_match = re.search(r">\s*(.+?)(?:\n\n|\n###)", section, re.DOTALL)
        proposed_rule = rule_match.group(1).strip() if rule_match else ""

        # Look for checked decisions
        decision = None
        edited_rule = None
        reject_reason = None

        # Check for "Approve as written"
        if re.search(r"\[x\]\s*Approve\s+as\s+written", section, re.IGNORECASE):
            decision = "approve"

        # Check for "Approve with edits"
        edit_match = re.search(
            r"\[x\]\s*Approve\s+with\s+edits:\s*`([^`]+)`",
            section,
            re.IGNORECASE,
        )
        if edit_match:
            decision = "approve_edited"
            edited_rule = edit_match.group(1).strip()

        # Check for "Reject"
        reject_match = re.search(
            r"\[x\]\s*Reject\s*\(reason:\s*([^)]+)\)",
            section,
            re.IGNORECASE,
        )
        if reject_match:
            decision = "reject"
            reject_reason = reject_match.group(1).strip()

        # Check for "Need more evidence"
        if re.search(r"\[x\]\s*Need\s+more\s+evidence", section, re.IGNORECASE):
            decision = "need_more"

        if decision:
            decisions.append(
                ReviewDecision(
                    rule_index=rule_index,
                    proposed_rule=proposed_rule,
                    decision=decision,
                    edited_rule=edited_rule,
                    reject_reason=reject_reason,
                )
            )

    return decisions


def apply_decision(db: Database, decision: ReviewDecision) -> bool:
    """Apply a review decision to the database.

    Returns True if successful.
    """
    now = datetime.utcnow().isoformat()

    if decision.decision == "approve":
        # Add to learned_rules
        db.execute(
            """
            INSERT INTO learned_rules
            (id, rule_text, source, active, created_at, approved_at)
            VALUES (?, ?, 'review', 1, ?, ?)
            """,
            (
                f"rule-{decision.rule_index}-{now[:10]}",
                decision.proposed_rule,
                now,
                now,
            ),
        )

        # Update review queue status
        db.execute(
            """
            UPDATE review_queue
            SET status = 'approved', reviewed_at = ?
            WHERE proposed_rule = ?
            """,
            (now, decision.proposed_rule),
        )

        return True

    elif decision.decision == "approve_edited":
        # Add edited rule to learned_rules
        rule_text = decision.edited_rule or decision.proposed_rule

        db.execute(
            """
            INSERT INTO learned_rules
            (id, rule_text, source, active, created_at, approved_at)
            VALUES (?, ?, 'review_edited', 1, ?, ?)
            """,
            (
                f"rule-{decision.rule_index}-{now[:10]}",
                rule_text,
                now,
                now,
            ),
        )

        # Update review queue status
        db.execute(
            """
            UPDATE review_queue
            SET status = 'approved', reviewed_at = ?
            WHERE proposed_rule = ?
            """,
            (now, decision.proposed_rule),
        )

        return True

    elif decision.decision == "reject":
        # Mark as rejected
        db.execute(
            """
            UPDATE review_queue
            SET status = 'rejected', reviewed_at = ?
            WHERE proposed_rule = ?
            """,
            (now, decision.proposed_rule),
        )

        return True

    elif decision.decision == "need_more":
        # Keep in queue but mark as needing more
        db.execute(
            """
            UPDATE review_queue
            SET status = 'needs_evidence', reviewed_at = ?
            WHERE proposed_rule = ?
            """,
            (now, decision.proposed_rule),
        )

        return True

    return False


def process_review_files(db: Database, config: ObsidianConfig) -> dict[str, int]:
    """Process all review files in the Obsidian vault.

    Returns counts of decisions processed.
    """
    reviews_path = config.output_path / "reviews"
    archive_path = config.output_path / "archive"

    if not reviews_path.exists():
        return {"processed": 0, "approved": 0, "rejected": 0, "pending": 0}

    counts = {"processed": 0, "approved": 0, "rejected": 0, "need_more": 0}

    # Find all pending review files
    for review_file in reviews_path.glob("*-pending.md"):
        decisions = parse_review_file(review_file)

        if not decisions:
            continue

        for decision in decisions:
            success = apply_decision(db, decision)
            if success:
                counts["processed"] += 1
                if decision.decision in ("approve", "approve_edited"):
                    counts["approved"] += 1
                elif decision.decision == "reject":
                    counts["rejected"] += 1
                elif decision.decision == "need_more":
                    counts["need_more"] += 1

        # If any decisions were processed, archive the file
        if decisions:
            # Get year-month for archive folder
            year_month = review_file.stem[:7]  # e.g., "2025-12"
            archive_subdir = archive_path / year_month
            archive_subdir.mkdir(parents=True, exist_ok=True)

            # Move to archive with processed suffix
            archive_name = review_file.stem + "-processed.md"
            shutil.move(str(review_file), str(archive_subdir / archive_name))

    return counts


def get_pending_review_count(db: Database) -> int:
    """Get count of items pending review."""
    result = db.fetchone(
        "SELECT COUNT(*) FROM review_queue WHERE status = 'pending'"
    )
    return result[0] if result else 0


def add_to_review_queue(
    db: Database,
    proposed_rule: str,
    rule_type: str,
    confidence: float,
    file_types: list[str] | None = None,
    project_scope: str | None = None,
    evidence: list[dict] | None = None,
) -> str:
    """Add a new item to the review queue.

    Returns the review item ID.
    """
    import uuid
    import json

    now = datetime.utcnow().isoformat()
    review_id = str(uuid.uuid4())[:16]

    db.execute(
        """
        INSERT INTO review_queue
        (id, rule_type, proposed_rule, file_types, project_scope, confidence, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            review_id,
            rule_type,
            proposed_rule,
            json.dumps(file_types) if file_types else None,
            project_scope,
            confidence,
            now,
        ),
    )

    # Add evidence
    if evidence:
        for ev in evidence:
            ev_id = str(uuid.uuid4())[:16]
            db.execute(
                """
                INSERT INTO review_evidence
                (id, review_id, conversation_id, project_path, timestamp,
                 trigger_message, evidence_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev_id,
                    review_id,
                    ev.get("conversation_id"),
                    ev.get("project_path"),
                    ev.get("timestamp"),
                    ev.get("message"),
                    ev.get("type", "explicit"),
                ),
            )

    return review_id
