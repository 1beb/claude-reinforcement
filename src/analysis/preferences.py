"""Preference extraction from conversations and corrections."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator
import uuid
import json

from src.db.database import Database
from src.analysis.corrections import DetectedCorrection
from src.analysis.classifier import get_file_type


@dataclass
class Preference:
    """An extracted preference."""

    id: str
    file_extension: str | None
    category: str  # 'formatting' | 'structure' | 'patterns' | 'workflow' | 'communication'
    preference_key: str
    preference_value: str
    evidence: list[dict]  # List of {conversation_id, message, timestamp}
    occurrence_count: int
    confidence: float
    first_seen: str
    last_seen: str


# Categories for preferences
PREFERENCE_CATEGORIES = {
    "formatting": [
        r"(?i)(format|style|indent|spacing|whitespace|line\s*length)",
        r"(?i)(pipe|operator|symbol)",
        r"(?i)(quote|string|apostrophe)",
    ],
    "structure": [
        r"(?i)(structure|organize|order|layout|arrange)",
        r"(?i)(header|section|comment|docstring)",
        r"(?i)(import|export|module)",
    ],
    "patterns": [
        r"(?i)(pattern|approach|method|technique|way)",
        r"(?i)(error\s*handling|validation|check)",
        r"(?i)(naming|convention|variable)",
    ],
    "workflow": [
        r"(?i)(test|build|run|render|compile|deploy)",
        r"(?i)(commit|git|branch|merge)",
        r"(?i)(step|process|workflow|procedure)",
    ],
    "communication": [
        r"(?i)(verbose|concise|brief|detailed)",
        r"(?i)(explain|describe|comment)",
        r"(?i)(emoji|formatting|markdown)",
    ],
}


def categorize_preference(text: str) -> str:
    """Determine the category of a preference based on its content."""
    text_lower = text.lower()

    for category, patterns in PREFERENCE_CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return category

    return "patterns"  # Default category


def generate_preference_key(text: str, file_ext: str | None) -> str:
    """Generate a unique key for a preference."""
    # Normalize the text
    normalized = text.lower().strip()

    # Remove common prefixes
    for prefix in ["please ", "always ", "never ", "don't ", "do not "]:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break

    # Truncate to reasonable length
    normalized = normalized[:100]

    # Create key with file extension if present
    if file_ext:
        return f"{file_ext}:{normalized[:50]}"
    return normalized[:50]


def extract_preference_from_correction(
    correction: DetectedCorrection,
) -> Preference | None:
    """Extract a preference from a detected correction."""
    if not correction.extracted_rule:
        return None

    # Determine file extension
    file_ext = correction.file_touched

    # Categorize
    category = categorize_preference(correction.extracted_rule)

    # Generate key
    pref_key = generate_preference_key(correction.extracted_rule, file_ext)

    return Preference(
        id=str(uuid.uuid4())[:16],
        file_extension=file_ext,
        category=category,
        preference_key=pref_key,
        preference_value=correction.extracted_rule,
        evidence=[
            {
                "conversation_id": correction.conversation_id,
                "message": correction.user_message,
                "timestamp": correction.timestamp,
                "project_path": correction.project_path,
            }
        ],
        occurrence_count=1,
        confidence=correction.confidence,
        first_seen=correction.timestamp,
        last_seen=correction.timestamp,
    )


def merge_preferences(existing: Preference, new: Preference) -> Preference:
    """Merge a new preference observation into an existing one."""
    # Combine evidence
    combined_evidence = existing.evidence + new.evidence

    # Update timestamps
    timestamps = [e.get("timestamp", "") for e in combined_evidence if e.get("timestamp")]
    first_seen = min(timestamps) if timestamps else existing.first_seen
    last_seen = max(timestamps) if timestamps else new.last_seen

    # Recalculate confidence based on occurrences
    occurrence_count = existing.occurrence_count + 1
    base_confidence = max(existing.confidence, new.confidence)

    # Boost confidence for repeated observations (max 0.4 boost)
    repetition_boost = min(occurrence_count * 0.1, 0.4)
    confidence = min(base_confidence + repetition_boost, 1.0)

    return Preference(
        id=existing.id,
        file_extension=existing.file_extension or new.file_extension,
        category=existing.category,
        preference_key=existing.preference_key,
        preference_value=existing.preference_value,  # Keep original wording
        evidence=combined_evidence[-10:],  # Keep last 10 pieces of evidence
        occurrence_count=occurrence_count,
        confidence=confidence,
        first_seen=first_seen,
        last_seen=last_seen,
    )


def save_preference(db: Database, pref: Preference) -> None:
    """Save or update a preference in the database."""
    evidence_json = json.dumps(pref.evidence)

    db.execute(
        """
        INSERT INTO file_type_preferences
        (id, file_extension, category, preference_key, preference_value,
         evidence, occurrence_count, confidence, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_extension, preference_key) DO UPDATE SET
            evidence = ?,
            occurrence_count = occurrence_count + 1,
            confidence = ?,
            last_seen = ?
        """,
        (
            pref.id,
            pref.file_extension or "",
            pref.category,
            pref.preference_key,
            pref.preference_value,
            evidence_json,
            pref.occurrence_count,
            pref.confidence,
            pref.first_seen,
            pref.last_seen,
            # For UPDATE
            evidence_json,
            pref.confidence,
            pref.last_seen,
        ),
    )


def get_preference(db: Database, file_ext: str | None, pref_key: str) -> Preference | None:
    """Get a preference by file extension and key."""
    result = db.fetchone(
        """
        SELECT id, file_extension, category, preference_key, preference_value,
               evidence, occurrence_count, confidence, first_seen, last_seen
        FROM file_type_preferences
        WHERE file_extension = ? AND preference_key = ?
        """,
        (file_ext or "", pref_key),
    )

    if result:
        return Preference(
            id=result[0],
            file_extension=result[1] or None,
            category=result[2],
            preference_key=result[3],
            preference_value=result[4],
            evidence=json.loads(result[5]) if result[5] else [],
            occurrence_count=result[6],
            confidence=result[7],
            first_seen=result[8],
            last_seen=result[9],
        )

    return None


def get_preferences_by_file_type(db: Database, file_ext: str) -> list[Preference]:
    """Get all preferences for a specific file type."""
    results = db.fetchall(
        """
        SELECT id, file_extension, category, preference_key, preference_value,
               evidence, occurrence_count, confidence, first_seen, last_seen
        FROM file_type_preferences
        WHERE file_extension = ? OR file_extension = ''
        ORDER BY confidence DESC
        """,
        (file_ext,),
    )

    preferences = []
    for row in results:
        preferences.append(
            Preference(
                id=row[0],
                file_extension=row[1] or None,
                category=row[2],
                preference_key=row[3],
                preference_value=row[4],
                evidence=json.loads(row[5]) if row[5] else [],
                occurrence_count=row[6],
                confidence=row[7],
                first_seen=row[8],
                last_seen=row[9],
            )
        )

    return preferences


def get_high_confidence_preferences(
    db: Database,
    min_confidence: float = 0.8,
) -> list[Preference]:
    """Get preferences above a confidence threshold."""
    results = db.fetchall(
        """
        SELECT id, file_extension, category, preference_key, preference_value,
               evidence, occurrence_count, confidence, first_seen, last_seen
        FROM file_type_preferences
        WHERE confidence >= ?
        ORDER BY file_extension, category, confidence DESC
        """,
        (min_confidence,),
    )

    preferences = []
    for row in results:
        preferences.append(
            Preference(
                id=row[0],
                file_extension=row[1] or None,
                category=row[2],
                preference_key=row[3],
                preference_value=row[4],
                evidence=json.loads(row[5]) if row[5] else [],
                occurrence_count=row[6],
                confidence=row[7],
                first_seen=row[8],
                last_seen=row[9],
            )
        )

    return preferences


def process_corrections_to_preferences(db: Database) -> int:
    """Process unreviewed corrections and extract preferences.

    Returns the number of preferences created/updated.
    """
    from src.analysis.corrections import get_unprocessed_corrections

    corrections = get_unprocessed_corrections(db)
    processed = 0

    for correction in corrections:
        pref = extract_preference_from_correction(correction)
        if pref:
            # Check if similar preference exists
            existing = get_preference(db, pref.file_extension, pref.preference_key)
            if existing:
                pref = merge_preferences(existing, pref)

            save_preference(db, pref)
            processed += 1

    return processed
