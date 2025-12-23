"""Preference and correction detection from conversation patterns."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator
import uuid
import json

from src.db.database import Database


@dataclass
class DetectedCorrection:
    """A detected preference or correction from a conversation."""

    id: str
    message_id: str
    target_msg_id: str | None
    correction_type: str  # 'explicit' | 'preference' | 'workflow' | 'tool' | 'documentation'
    user_message: str
    assistant_message: str | None
    extracted_rule: str | None
    confidence: float
    conversation_id: str
    project_path: str
    timestamp: str
    file_touched: str | None = None


# Patterns that indicate preferences/corrections
# Format: (pattern, type, base_confidence)
PREFERENCE_PATTERNS = [
    # === CORRECTIONS (something went wrong) ===
    # Direct negation
    (r"(?i)^no[,.]?\s+(.+)", "correction", 0.9),
    (r"(?i)^wrong[,.]?\s+(.+)", "correction", 0.9),
    (r"(?i)^incorrect[,.]?\s+(.+)", "correction", 0.85),
    (r"(?i)^that'?s?\s+not\s+(right|correct|what)", "correction", 0.85),

    # Instruction corrections
    (r"(?i)don'?t\s+(.+)", "correction", 0.8),
    (r"(?i)never\s+(.+)", "correction", 0.85),
    (r"(?i)^why\s+(didn'?t|don'?t)\s+you", "correction", 0.7),

    # === TOOL PREFERENCES ===
    # "use X" / "use X for Y" / "you should use X"
    (r"(?i)(?:you\s+should\s+)?use\s+(\w+)(?:\s+for\s+|\s+when\s+|\s+instead)", "tool", 0.85),
    (r"(?i)(?:please\s+)?use\s+(\w+)\s+(?:not|instead\s+of)\s+(\w+)", "tool", 0.9),
    (r"(?i)for\s+python\s+(?:projects?\s+)?(?:you\s+should\s+)?(?:always\s+)?use\s+(\w+)", "tool", 0.9),
    (r"(?i)(?:always\s+)?use\s+(uv|pip|poetry|conda|npm|yarn|pnpm|bun)", "tool", 0.85),

    # === WORKFLOW PREFERENCES ===
    # "always X" / "make sure to X" / "remember to X"
    (r"(?i)always\s+(.+)", "workflow", 0.85),
    (r"(?i)make\s+sure\s+(?:to\s+|you\s+)?(.+)", "workflow", 0.8),
    (r"(?i)remember\s+to\s+(.+)", "workflow", 0.8),
    (r"(?i)you\s+should\s+(?:always\s+)?(.+)", "workflow", 0.75),
    (r"(?i)please\s+(?:always\s+)?(.+?)(?:\s+when|\s+for|\s+before|\s+after|$)", "workflow", 0.7),

    # === DOCUMENTATION PREFERENCES ===
    # Update/add to readme, changelog, etc.
    (r"(?i)(?:please\s+)?(?:also\s+)?update\s+(?:the\s+)?(readme|changelog|history|documentation|docs)", "documentation", 0.85),
    (r"(?i)(?:please\s+)?(?:also\s+)?add\s+(?:this\s+)?(?:to\s+)?(?:the\s+)?(readme|changelog|history|documentation|docs)", "documentation", 0.85),
    (r"(?i)(?:please\s+)?document\s+(?:this|the|your)", "documentation", 0.8),
    (r"(?i)(?:please\s+)?(?:also\s+)?write\s+(?:a\s+)?(?:the\s+)?(readme|documentation|docs)", "documentation", 0.8),
    (r"(?i)keep\s+(?:the\s+)?(readme|changelog|documentation|docs)\s+(?:up\s+to\s+date|updated)", "documentation", 0.85),

    # === STYLE PREFERENCES ===
    (r"(?i)i\s+prefer\s+(.+)", "preference", 0.75),
    (r"(?i)use\s+(.+)\s+instead(\s+of\s+.+)?", "preference", 0.8),
    (r"(?i)instead\s+of\s+(.+),?\s+(use|do)\s+(.+)", "preference", 0.8),

    # Refinement patterns
    (r"(?i)make\s+it\s+(more|less)\s+(.+)", "refinement", 0.6),
    (r"(?i)too\s+(verbose|complex|long|short|simple)", "refinement", 0.65),
    (r"(?i)(?:be\s+)?more\s+concise", "refinement", 0.65),

    # === REPEATED REQUESTS (hints at missing workflow) ===
    (r"(?i)^did\s+you\s+(try|check|run|test|render|update)", "reminder", 0.6),
    (r"(?i)^can\s+you\s+(?:also|actually|please)", "reminder", 0.5),

    # === FILE-SPECIFIC PATTERNS ===
    (r"(?i)when\s+working\s+(?:with|on)\s+(\.\w+)\s+files?,?\s+(.+)", "file_specific", 0.8),
    (r"(?i)for\s+(\.\w+)\s+files?,?\s+(.+)", "file_specific", 0.75),
    (r"(?i)in\s+(\.\w+)\s+files?,?\s+(?:always\s+)?(.+)", "file_specific", 0.75),
]

# Legacy alias
CORRECTION_PATTERNS = PREFERENCE_PATTERNS

# Patterns that indicate positive feedback (not corrections)
POSITIVE_PATTERNS = [
    r"(?i)^(perfect|great|thanks|thank\s+you|good|nice|excellent|awesome)",
    r"(?i)^that'?s?\s+(right|correct|good|perfect|great)",
    r"(?i)^(yes|yeah|yep|yup)[,.]?\s*(that'?s?|looks?)?",
    r"(?i)^exactly",
    r"(?i)^(lgtm|looks\s+good)",
]

# Patterns that indicate pasted/noise content (not actual preferences)
NOISE_INDICATORS = [
    # Pasted logs/output
    r"^\d{4}-\d{2}-\d{2}",  # Timestamp at start (log output)
    r"^[A-Z][a-z]+\s+\d{1,2},\s+\d{4}",  # "Nov 29, 2025" date format
    r"^\s*\d+\.\d+\.\d+",  # Version numbers at start
    r"^(Error|Warning|INFO|DEBUG|WARN):",  # Log levels
    r"^\s*(GET|POST|PUT|DELETE|PATCH)\s+/",  # HTTP requests
    r"Traceback \(most recent call last\)",  # Python tracebacks
    r"^\s*at\s+\w+\.\w+\(",  # Stack traces
    r"^npm\s+(ERR|WARN)!",  # npm output

    # Debugging context (user pasting errors)
    r"(?i)I'?m\s+(?:still\s+)?getting\s+(?:a\s+)?(?:an?\s+)?(?:error|exception|\d{3})",
    r"(?i)getting\s+a\s+\d{3}",  # "getting a 401"
    r"@\w+/\w+:dev:",  # npm workspace dev output
    r"✓\s+(?:Starting|Compiled|Ready)",  # Build output
    r"○\s+Compiling",  # Next.js compiling

    # UI/menu content
    r"^(Start|File|Edit|View|Help)\s*$",  # Menu items
    r"^\s*(Open|Save|Close|New)\s+(File|Folder)",  # File menu items
    r"^Recent\s*$",  # UI element
    r"Walkthroughs",  # VS Code UI
    r"^\s*\[\s*\d+\s*\]",  # Numbered output lines

    # System content (IDE/Claude Code injected messages)
    r"<system-reminder>",  # System messages
    r"<ide_opened_file>",  # IDE file open notifications
    r"<ide_selection>",  # IDE selection context
    r"<ide_",  # Catch-all for other IDE tags
    r"<user-prompt-submit-hook>",  # Hook messages
    r"^Base directory for this skill:",  # Skill file content
    r"^# .{50,}",  # Very long markdown headers (likely pasted docs)

    # Deploy/CI output
    r"(Starting|Stopping)\s+Container",
    r"Successfully\s+(built|deployed|installed)",
    r"Downloading\s+\w+",
    r"^\s*━+",  # Progress bars

    # Very short fragments that aren't actionable
    r"^.{1,15}$",  # Too short to be meaningful (unless specific)
]


def is_positive_feedback(text: str) -> bool:
    """Check if a message is positive feedback."""
    for pattern in POSITIVE_PATTERNS:
        if re.search(pattern, text.strip()):
            return True
    return False


def is_noise_content(text: str) -> bool:
    """Check if a message looks like pasted/noise content rather than a preference."""
    # Skip very long messages (likely pasted content)
    if len(text) > 1000:
        return True

    # Check for noise indicators
    for pattern in NOISE_INDICATORS:
        if re.search(pattern, text, re.MULTILINE):
            return True

    # Check for high ratio of special characters (likely code/logs)
    special_chars = sum(1 for c in text if c in '{}[]()<>|&;$`\\')
    if len(text) > 50 and special_chars / len(text) > 0.15:
        return True

    # Check for many newlines (likely pasted multi-line content)
    newline_count = text.count('\n')
    if newline_count > 10:
        return True

    return False


def is_task_request(text: str) -> bool:
    """Check if a message is a task request rather than behavioral feedback."""
    # Patterns for task requests (not behavioral guidance)
    task_patterns = [
        # Direct requests anywhere in message
        r"(?i)(?:can you|could you|would you|please)\s+(?:also\s+)?(?:update|add|create|write|fix|change|modify|remove|delete|run|test|check|show|make|build)",
        r"(?i)(?:I need|I want|I'd like)\s+(?:you\s+)?(?:to\s+)?",
        r"(?i)(?:let's|lets)\s+(?:add|create|write|fix|change|modify|build|test|run|make\s+sure|check)",
        # Imperative task instructions
        r"(?i)^(?:update|add|create|write|fix|change|modify|remove|delete|run|test)\s+",
        # "this" references specific context
        r"(?i)write\s+this\s+as\s+",
        r"(?i)change\s+this\s+to\s+",
        r"(?i)make\s+this\s+",
        r"(?i)make\s+sure\s+(?:this|that|it)",
    ]
    for pattern in task_patterns:
        if re.search(pattern, text):
            return True
    return False


def is_behavioral_preference(text: str) -> bool:
    """Check if text looks like a generalizable behavioral preference vs task-specific."""
    # Questions are not preferences (check only at end of sentence)
    if text.strip().endswith("?"):
        return False

    # Very short texts are usually fragments
    if len(text.strip()) < 20:
        return False

    behavioral_signals = [
        r"(?i)\balways\b",
        r"(?i)\bnever\b",
        r"(?i)\bwhen\s+working\b",
        r"(?i)\bfor\s+\w+\s+(?:projects?|files?)\b",
        r"(?i)\bin\s+(?:all|every)\b",
        r"(?i)\bby\s+default\b",
        r"(?i)\bprefer\b",
        r"(?i)\bgoing\s+forward\b",
        r"(?i)\bfrom\s+now\s+on\b",
        r"(?i)\bmake\s+sure\s+to\b",
        r"(?i)\bremember\s+to\b",
        r"(?i)\bshould\s+(?:always|never)\b",
        # Corrections about behavior (not task-specific)
        r"(?i)^(?:don'?t|never)\s+\w+\s+\w+",  # "Don't use emojis" etc.
        r"(?i)\binstead\s+of\b.*\bfor\b",  # "instead of X for Y projects"
    ]
    for pattern in behavioral_signals:
        if re.search(pattern, text):
            return True
    return False


def extract_correction_rule(user_message: str) -> str | None:
    """Try to extract a rule from a correction message."""
    text = user_message.strip()

    # Skip task requests - these are not behavioral feedback
    if is_task_request(text):
        return None

    # Must have behavioral signals
    if not is_behavioral_preference(text):
        return None

    # Check if message contains negation anywhere - if so, preserve full context
    # "please don't guess" should NOT become "guess"
    negation_patterns = [
        r"(?i)\bdon'?t\s+\w+",
        r"(?i)\bnever\s+\w+",
        r"(?i)\bno\s+need\b",
        r"(?i)\bnot\s+\w+",
        r"(?i)\bavoid\s+\w+",
    ]
    has_negation = any(re.search(p, text) for p in negation_patterns)

    if has_negation:
        # Return the full message to preserve negation context
        if len(text) < 200:
            return text
        # For longer messages, try to extract the negation clause
        for pattern in negation_patterns:
            match = re.search(pattern + r"[^.!?]*", text)
            if match:
                clause = match.group(0)
                if len(clause) > 15:
                    return clause.strip()
        return None

    # Try to extract the actionable part for non-negation patterns
    for pattern, _, _ in CORRECTION_PATTERNS:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            if groups:
                for group in groups:
                    if group and len(group) > 15:
                        return group.strip()
                # If captured group is too short, return full message if reasonable length
                if len(text) < 200:
                    return text
                return None

    # If no pattern matched but has behavioral signals, return the whole message
    if len(text) < 200:
        return text

    return None


def extract_file_reference(text: str) -> str | None:
    """Extract file extension or type referenced in a message."""
    # Look for file extensions
    ext_match = re.search(r"\.(\w{1,10})\b", text)
    if ext_match:
        return f".{ext_match.group(1)}"

    # Look for file type names
    type_patterns = [
        (r"(?i)\b(python|py)\b", ".py"),
        (r"(?i)\b(typescript|ts)\b", ".ts"),
        (r"(?i)\b(javascript|js)\b", ".js"),
        (r"(?i)\b(quarto|qmd)\b", ".qmd"),
        (r"(?i)\b(r\s+files?|\.r\b)", ".R"),
        (r"(?i)\b(markdown|md)\b", ".md"),
        (r"(?i)\b(rust|rs)\b", ".rs"),
        (r"(?i)\b(go|golang)\b", ".go"),
    ]

    for pattern, ext in type_patterns:
        if re.search(pattern, text):
            return ext

    return None


def detect_corrections_in_conversation(
    db: Database,
    conversation_id: str,
) -> list[DetectedCorrection]:
    """Detect corrections in a single conversation."""
    # Get conversation info
    conv_result = db.fetchone(
        "SELECT project_path FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    if not conv_result:
        return []

    project_path = conv_result[0]

    # Get messages ordered by timestamp
    messages = db.fetchall(
        """
        SELECT id, role, content, timestamp, parent_uuid
        FROM messages
        WHERE conversation_id = ?
        ORDER BY timestamp
        """,
        (conversation_id,),
    )

    corrections: list[DetectedCorrection] = []
    prev_assistant_msg: tuple | None = None

    for msg in messages:
        msg_id, role, content, timestamp, parent_uuid = msg

        if role == "assistant":
            prev_assistant_msg = msg
            continue

        if role != "user":
            continue

        # Skip positive feedback
        if is_positive_feedback(content):
            prev_assistant_msg = None
            continue

        # Skip noise/pasted content
        if is_noise_content(content):
            prev_assistant_msg = None
            continue

        # Check for preference patterns
        for pattern, correction_type, base_confidence in PREFERENCE_PATTERNS:
            match = re.search(pattern, content)
            if match:
                # Extract what we can
                extracted_rule = extract_correction_rule(content)

                # Skip if no actionable rule was extracted
                if not extracted_rule:
                    continue

                file_touched = extract_file_reference(content)

                # Adjust confidence based on context
                confidence = base_confidence
                if prev_assistant_msg:
                    # Higher confidence if there was a recent assistant message
                    confidence += 0.05
                if file_touched:
                    # Higher confidence if file-specific
                    confidence += 0.05

                # Cap single-occurrence confidence - real confidence comes from repetition
                # A single observation should never be above 0.65
                confidence = min(confidence, 0.65)

                corrections.append(
                    DetectedCorrection(
                        id=str(uuid.uuid4())[:16],
                        message_id=msg_id,
                        target_msg_id=prev_assistant_msg[0] if prev_assistant_msg else None,
                        correction_type=correction_type,
                        user_message=content,
                        assistant_message=prev_assistant_msg[2] if prev_assistant_msg else None,
                        extracted_rule=extracted_rule,
                        confidence=min(confidence, 1.0),
                        conversation_id=conversation_id,
                        project_path=project_path,
                        timestamp=timestamp,
                        file_touched=file_touched,
                    )
                )
                break  # Only one correction per message

        prev_assistant_msg = None  # Reset after processing user message

    return corrections


def detect_all_corrections(db: Database) -> Iterator[DetectedCorrection]:
    """Detect corrections in all conversations."""
    # Get all conversation IDs
    conversations = db.fetchall("SELECT id FROM conversations")

    for (conv_id,) in conversations:
        corrections = detect_corrections_in_conversation(db, conv_id)
        yield from corrections


def save_correction(db: Database, correction: DetectedCorrection) -> None:
    """Save a detected correction to the database."""
    db.execute(
        """
        INSERT OR REPLACE INTO corrections
        (id, message_id, target_msg_id, correction_type, extracted_rule, confidence, reviewed, approved)
        VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
        """,
        (
            correction.id,
            correction.message_id,
            correction.target_msg_id,
            correction.correction_type,
            correction.extracted_rule,
            correction.confidence,
        ),
    )


def get_unprocessed_corrections(db: Database) -> list[DetectedCorrection]:
    """Get corrections that haven't been reviewed yet."""
    results = db.fetchall(
        """
        SELECT c.id, c.message_id, c.target_msg_id, c.correction_type,
               c.extracted_rule, c.confidence, m.content, m.timestamp,
               conv.project_path, conv.id
        FROM corrections c
        JOIN messages m ON c.message_id = m.id
        JOIN conversations conv ON m.conversation_id = conv.id
        WHERE c.reviewed = 0
        ORDER BY c.confidence DESC
        """
    )

    corrections = []
    for row in results:
        corrections.append(
            DetectedCorrection(
                id=row[0],
                message_id=row[1],
                target_msg_id=row[2],
                correction_type=row[3],
                extracted_rule=row[4],
                confidence=row[5],
                user_message=row[6],
                assistant_message=None,  # Would need another query
                timestamp=row[7],
                project_path=row[8],
                conversation_id=row[9],
            )
        )

    return corrections
