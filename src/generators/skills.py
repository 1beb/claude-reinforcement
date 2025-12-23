"""Skill generator for auto-creating .claude/skills/ from workflow patterns."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json
import re

from src.db.database import Database


@dataclass
class SkillCandidate:
    """A candidate skill detected from conversation patterns."""

    name: str
    description: str
    trigger: str
    steps: list[str]
    evidence: list[str]  # Original messages that led to this
    confidence: float
    project_scope: str | None = None


# Workflow patterns that suggest skills
WORKFLOW_PATTERNS = [
    {
        "name": "task-workflow",
        "triggers": [
            r"(?i)branch.*test.*commit",
            r"(?i)create.*branch.*push",
            r"(?i)implement.*test.*push",
        ],
        "description": "Use when starting any development task that involves code changes",
    },
    {
        "name": "safe-git-commit",
        "triggers": [
            r"(?i)never.*git\s+add\s+\.",
            r"(?i)don'?t.*git\s+add\s+\.",
            r"(?i)specify.*files.*explicitly",
        ],
        "description": "Use when committing changes - ensures safe git practices",
    },
    {
        "name": "pre-commit-testing",
        "triggers": [
            r"(?i)run.*tests.*before.*commit",
            r"(?i)tests.*must.*pass",
            r"(?i)verify.*tests.*push",
        ],
        "description": "Use before committing - ensures tests pass first",
    },
    {
        "name": "python-environment",
        "triggers": [
            r"(?i)use\s+uv\b",
            r"(?i)uv.*instead.*pip",
            r"(?i)python.*projects?.*uv",
        ],
        "description": "Use when setting up Python projects - ensures correct tooling",
    },
]


def detect_workflow_patterns(
    db: Database,
    min_occurrences: int = 2,
) -> list[SkillCandidate]:
    """Detect workflow patterns from corrections and preferences.

    Returns skill candidates that appear multiple times.
    """
    # Get all rules and corrections
    rules = db.fetchall(
        """
        SELECT proposed_rule, confidence, project_scope
        FROM review_queue
        WHERE status = 'approved'
        """
    )

    corrections = db.fetchall(
        """
        SELECT extracted_rule, confidence
        FROM corrections
        WHERE reviewed = 1 AND approved = 1
        """
    )

    # Combine all evidence
    all_rules = [(r[0], r[1], r[2]) for r in rules] + [(c[0], c[1], None) for c in corrections]

    candidates: dict[str, SkillCandidate] = {}

    for pattern in WORKFLOW_PATTERNS:
        matches = []

        for rule_text, confidence, project_scope in all_rules:
            if not rule_text:
                continue

            for trigger in pattern["triggers"]:
                if re.search(trigger, rule_text):
                    matches.append((rule_text, confidence, project_scope))
                    break

        if len(matches) >= min_occurrences:
            # Aggregate evidence
            evidence = [m[0] for m in matches]
            avg_confidence = sum(m[1] for m in matches) / len(matches)

            # Determine scope (global if mixed, else project-specific)
            scopes = set(m[2] for m in matches if m[2])
            project_scope = list(scopes)[0] if len(scopes) == 1 else None

            candidates[pattern["name"]] = SkillCandidate(
                name=pattern["name"],
                description=pattern["description"],
                trigger=pattern["description"].replace("Use when ", "").replace(" - ", ": "),
                steps=extract_steps_from_evidence(evidence),
                evidence=evidence[:5],  # Keep top 5
                confidence=avg_confidence,
                project_scope=project_scope,
            )

    return list(candidates.values())


def extract_steps_from_evidence(evidence: list[str]) -> list[str]:
    """Extract actionable steps from evidence messages."""
    steps = []

    # Common step patterns
    step_patterns = [
        r"(?i)(create|make).*branch",
        r"(?i)run.*test",
        r"(?i)commit.*change",
        r"(?i)push.*remote",
        r"(?i)verify.*pass",
    ]

    for pattern in step_patterns:
        for msg in evidence:
            if re.search(pattern, msg):
                # Clean and add as step
                match = re.search(pattern, msg)
                if match:
                    steps.append(match.group(0).capitalize())
                break

    # Deduplicate while preserving order
    seen = set()
    unique_steps = []
    for step in steps:
        if step.lower() not in seen:
            seen.add(step.lower())
            unique_steps.append(step)

    return unique_steps if unique_steps else ["Follow the workflow as described"]


def generate_skill_content(candidate: SkillCandidate) -> str:
    """Generate markdown content for a skill file."""
    lines = [
        "---",
        f"name: {candidate.name}",
        f"description: {candidate.description}",
        "---",
        "",
        f"# {candidate.name.replace('-', ' ').title()}",
        "",
        f"_Auto-generated by claude-reinforcement on {datetime.utcnow().strftime('%Y-%m-%d')}_",
        "",
        "## When to Use",
        "",
        candidate.trigger,
        "",
        "## Steps",
        "",
    ]

    for i, step in enumerate(candidate.steps, 1):
        lines.append(f"{i}. {step}")

    lines.extend([
        "",
        "## Evidence",
        "",
        "This skill was generated from the following corrections:",
        "",
    ])

    for evidence in candidate.evidence:
        # Truncate long evidence
        truncated = evidence[:100] + "..." if len(evidence) > 100 else evidence
        lines.append(f"- {truncated}")

    lines.append("")
    return "\n".join(lines)


def write_skill_file(
    candidate: SkillCandidate,
    output_dir: Path,
) -> bool:
    """Write a skill file to the output directory.

    Returns True if file was written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / f"{candidate.name}.md"

    # Don't overwrite manually created skills
    if file_path.exists():
        existing = file_path.read_text()
        if "Auto-generated by claude-reinforcement" not in existing:
            return False

    content = generate_skill_content(candidate)
    file_path.write_text(content)
    return True


def generate_skills_for_project(
    db: Database,
    project_path: Path,
    min_occurrences: int = 2,
) -> dict[str, int]:
    """Generate skill files for a project.

    Returns count of skills written.
    """
    candidates = detect_workflow_patterns(db, min_occurrences)

    skills_dir = project_path / ".claude" / "skills"
    written = 0

    for candidate in candidates:
        # Filter by project scope if applicable
        if candidate.project_scope and candidate.project_scope != str(project_path):
            continue

        if write_skill_file(candidate, skills_dir):
            written += 1

    return {"skills": written}


def generate_global_skills(
    db: Database,
    claude_dir: Path,
    min_occurrences: int = 2,
) -> dict[str, int]:
    """Generate global skill files.

    Returns count of skills written.
    """
    candidates = detect_workflow_patterns(db, min_occurrences)

    skills_dir = claude_dir / "skills"
    written = 0

    for candidate in candidates:
        # Only write global skills (no project scope)
        if candidate.project_scope:
            continue

        if write_skill_file(candidate, skills_dir):
            written += 1

    return {"skills": written}


def update_all_skills(
    db: Database,
    global_claude_dir: Path,
    project_paths: list[Path] | None = None,
    min_occurrences: int = 2,
) -> dict[str, int]:
    """Update all skill files.

    Returns counts of skills written.
    """
    counts = {"global_skills": 0, "project_skills": 0}

    # Generate global skills
    result = generate_global_skills(db, global_claude_dir, min_occurrences)
    counts["global_skills"] = result["skills"]

    # Generate project-specific skills
    if project_paths:
        for project_path in project_paths:
            result = generate_skills_for_project(db, project_path, min_occurrences)
            counts["project_skills"] += result["skills"]
    else:
        results = db.fetchall(
            "SELECT DISTINCT project_path FROM conversations"
        )
        for (project_path_str,) in results:
            project_path = Path(project_path_str)
            if project_path.exists():
                result = generate_skills_for_project(db, project_path, min_occurrences)
                counts["project_skills"] += result["skills"]

    return counts
