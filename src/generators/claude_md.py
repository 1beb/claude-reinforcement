"""CLAUDE.md and .claude/rules/ file generator.

Supports two output modes:
1. Legacy: Append to CLAUDE.md with markers (backward compatible)
2. Modern: Write to .claude/rules/ with YAML frontmatter (recommended)
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator
import json

from src.db.database import Database
from src.analysis.classifier import get_classification, get_parent_types


# Mapping from file extensions to glob patterns
FILE_TYPE_TO_GLOB = {
    ".py": ["**/*.py"],
    ".js": ["**/*.js"],
    ".ts": ["**/*.ts"],
    ".jsx": ["**/*.jsx"],
    ".tsx": ["**/*.tsx"],
    ".qmd": ["**/*.qmd"],
    ".md": ["**/*.md"],
    ".R": ["**/*.R", "**/*.r"],
    ".rs": ["**/*.rs"],
    ".go": ["**/*.go"],
}

# Category to filename mapping
CATEGORY_TO_FILENAME = {
    "General": "general.md",
    "File-Specific": "file-specific.md",
    "Code Style": "code-style.md",
    "Workflow": "workflow.md",
    "Communication": "communication.md",
    "Testing": "testing.md",
    "Documentation": "documentation.md",
    "Software": "software.md",
}


@dataclass
class Rule:
    """A learned rule."""

    id: str
    rule_text: str
    source: str
    project_scope: str | None
    project_type: str | None
    file_types: list[str]
    active: bool
    created_at: str
    approved_at: str | None


def get_active_rules(db: Database) -> list[Rule]:
    """Get all active learned rules."""
    results = db.fetchall(
        """
        SELECT id, rule_text, source, project_scope, project_type,
               file_types, active, created_at, approved_at
        FROM learned_rules
        WHERE active = 1
        ORDER BY approved_at DESC
        """
    )

    rules = []
    for row in results:
        file_types = json.loads(row[5]) if row[5] else []
        rules.append(
            Rule(
                id=row[0],
                rule_text=row[1],
                source=row[2],
                project_scope=row[3],
                project_type=row[4],
                file_types=file_types,
                active=bool(row[6]),
                created_at=row[7],
                approved_at=row[8],
            )
        )

    return rules


def get_rules_for_project(
    db: Database,
    project_path: str,
) -> list[Rule]:
    """Get rules applicable to a specific project.

    Returns rules in order of specificity:
    1. Project-specific rules
    2. Project-type rules (with inheritance)
    3. Global rules
    """
    all_rules = get_active_rules(db)

    # Get project classification
    classification = get_classification(db, project_path)
    project_type = classification.project_type if classification else None

    # Get type hierarchy
    type_chain = get_parent_types(project_type) if project_type else []

    applicable_rules = []

    for rule in all_rules:
        # Check project scope
        if rule.project_scope:
            if rule.project_scope != project_path:
                continue

        # Check project type
        if rule.project_type:
            if rule.project_type not in type_chain:
                continue

        applicable_rules.append(rule)

    return applicable_rules


def group_rules_by_category(rules: list[Rule]) -> dict[str, list[Rule]]:
    """Group rules by their likely category."""
    categories: dict[str, list[Rule]] = {
        "General": [],
        "File-Specific": [],
        "Code Style": [],
        "Workflow": [],
        "Communication": [],
    }

    for rule in rules:
        text_lower = rule.rule_text.lower()

        if rule.file_types:
            categories["File-Specific"].append(rule)
        elif any(kw in text_lower for kw in ["commit", "git", "test", "build", "run"]):
            categories["Workflow"].append(rule)
        elif any(kw in text_lower for kw in ["concise", "verbose", "emoji", "format"]):
            categories["Communication"].append(rule)
        elif any(kw in text_lower for kw in ["indent", "style", "naming", "pipe"]):
            categories["Code Style"].append(rule)
        else:
            categories["General"].append(rule)

    # Remove empty categories
    return {k: v for k, v in categories.items() if v}


def format_rules_section(category: str, rules: list[Rule]) -> str:
    """Format a section of rules as markdown."""
    lines = [f"## {category}\n"]

    for rule in rules:
        # Format file types if present
        if rule.file_types:
            types_str = ", ".join(rule.file_types)
            lines.append(f"- ({types_str}) {rule.rule_text}")
        else:
            lines.append(f"- {rule.rule_text}")

    lines.append("")  # Blank line after section
    return "\n".join(lines)


def generate_claude_md(
    db: Database,
    project_path: str | None = None,
    include_header: bool = True,
) -> str:
    """Generate CLAUDE.md content.

    If project_path is provided, generates project-specific rules.
    Otherwise, generates global rules.
    """
    if project_path:
        rules = get_rules_for_project(db, project_path)
        title = f"Project Instructions - {Path(project_path).name}"
    else:
        # Get only global rules
        all_rules = get_active_rules(db)
        rules = [r for r in all_rules if not r.project_scope]
        title = "Claude Code Instructions"

    if not rules:
        return ""

    # Group rules
    grouped = group_rules_by_category(rules)

    # Generate content
    lines = []

    if include_header:
        lines.append(f"# {title}\n")
        lines.append(
            f"_Auto-generated by claude-reinforcement on {datetime.utcnow().strftime('%Y-%m-%d')}_\n"
        )

    for category, category_rules in grouped.items():
        lines.append(format_rules_section(category, category_rules))

    return "\n".join(lines)


def write_global_claude_md(db: Database, claude_dir: Path) -> bool:
    """Write the global CLAUDE.md file.

    Returns True if file was written.
    """
    content = generate_claude_md(db, project_path=None)

    if not content:
        return False

    # Ensure directory exists
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Read existing file to preserve manual content
    claude_md_path = claude_dir / "CLAUDE.md"
    existing_content = ""

    if claude_md_path.exists():
        existing_content = claude_md_path.read_text()

    # Look for auto-generated section marker
    marker_start = "<!-- BEGIN CLAUDE-REINFORCEMENT -->"
    marker_end = "<!-- END CLAUDE-REINFORCEMENT -->"

    if marker_start in existing_content:
        # Replace existing auto-generated section
        before = existing_content.split(marker_start)[0]
        after_parts = existing_content.split(marker_end)
        after = after_parts[1] if len(after_parts) > 1 else ""

        new_content = f"{before}{marker_start}\n{content}\n{marker_end}{after}"
    else:
        # Append to end of file
        new_content = f"{existing_content}\n\n{marker_start}\n{content}\n{marker_end}\n"

    claude_md_path.write_text(new_content)
    return True


def write_project_claude_md(db: Database, project_path: Path) -> bool:
    """Write a project-specific CLAUDE.md file.

    Returns True if file was written.
    """
    content = generate_claude_md(db, project_path=str(project_path))

    if not content:
        return False

    # Write to .claude/CLAUDE.md in project
    claude_dir = project_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    claude_md_path = claude_dir / "CLAUDE.md"

    # Same logic as global - preserve manual content
    existing_content = ""
    if claude_md_path.exists():
        existing_content = claude_md_path.read_text()

    marker_start = "<!-- BEGIN CLAUDE-REINFORCEMENT -->"
    marker_end = "<!-- END CLAUDE-REINFORCEMENT -->"

    if marker_start in existing_content:
        before = existing_content.split(marker_start)[0]
        after_parts = existing_content.split(marker_end)
        after = after_parts[1] if len(after_parts) > 1 else ""
        new_content = f"{before}{marker_start}\n{content}\n{marker_end}{after}"
    else:
        new_content = f"{existing_content}\n\n{marker_start}\n{content}\n{marker_end}\n"

    claude_md_path.write_text(new_content)
    return True


def update_all_claude_md_files(
    db: Database,
    global_claude_dir: Path,
    project_paths: list[Path] | None = None,
) -> dict[str, int]:
    """Update all CLAUDE.md files.

    Returns counts of files updated.
    """
    counts = {"global": 0, "projects": 0}

    # Update global
    if write_global_claude_md(db, global_claude_dir):
        counts["global"] = 1

    # Update project-specific files
    if project_paths:
        for project_path in project_paths:
            if write_project_claude_md(db, project_path):
                counts["projects"] += 1
    else:
        # Get projects from database
        results = db.fetchall(
            "SELECT DISTINCT project_path FROM conversations"
        )
        for (project_path_str,) in results:
            project_path = Path(project_path_str)
            if project_path.exists():
                if write_project_claude_md(db, project_path):
                    counts["projects"] += 1

    return counts


# =============================================================================
# Modern Format: .claude/rules/ with YAML frontmatter
# =============================================================================


def file_types_to_paths(file_types: list[str]) -> list[str]:
    """Convert file extensions to glob patterns for YAML frontmatter."""
    paths = []
    for ft in file_types:
        if ft in FILE_TYPE_TO_GLOB:
            paths.extend(FILE_TYPE_TO_GLOB[ft])
        else:
            # Handle unknown extensions
            ext = ft.lstrip(".")
            paths.append(f"**/*.{ext}")
    return paths


def generate_yaml_frontmatter(paths: list[str] | None = None) -> str:
    """Generate YAML frontmatter for a rule file."""
    if not paths:
        return ""

    paths_str = ", ".join(f'"{p}"' for p in paths)
    return f"---\npaths: [{paths_str}]\n---\n\n"


def format_rules_file_modern(
    category: str,
    rules: list[Rule],
    include_paths: bool = True,
) -> str:
    """Format a category of rules as a modern rule file with frontmatter."""
    lines = []

    # Collect all file types for this category
    all_file_types: set[str] = set()
    for rule in rules:
        all_file_types.update(rule.file_types)

    # Add YAML frontmatter if there are file-type-specific rules
    if include_paths and all_file_types:
        paths = file_types_to_paths(list(all_file_types))
        lines.append(generate_yaml_frontmatter(paths))

    # Add header
    lines.append(f"# {category} Rules\n")
    lines.append(
        f"_Auto-generated by claude-reinforcement on {datetime.utcnow().strftime('%Y-%m-%d')}_\n"
    )

    # Add rules
    for rule in rules:
        if rule.file_types:
            types_str = ", ".join(rule.file_types)
            lines.append(f"- ({types_str}) {rule.rule_text}")
        else:
            lines.append(f"- {rule.rule_text}")

    lines.append("")
    return "\n".join(lines)


def write_rules_directory(
    db: Database,
    project_path: Path,
) -> dict[str, int]:
    """Write rules to .claude/rules/ directory with proper structure.

    Returns counts of files written.
    """
    rules = get_rules_for_project(db, str(project_path))

    if not rules:
        return {"files": 0}

    # Group rules
    grouped = group_rules_by_category(rules)

    # Create rules directory
    rules_dir = project_path / ".claude" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    files_written = 0

    for category, category_rules in grouped.items():
        filename = CATEGORY_TO_FILENAME.get(category, f"{category.lower().replace(' ', '-')}.md")
        file_path = rules_dir / filename

        # Check if file exists and has manual content
        if file_path.exists():
            existing = file_path.read_text()
            # Skip if file exists and doesn't have our marker
            if "Auto-generated by claude-reinforcement" not in existing:
                continue

        content = format_rules_file_modern(category, category_rules)
        file_path.write_text(content)
        files_written += 1

    return {"files": files_written}


def write_global_rules_directory(
    db: Database,
    claude_dir: Path,
) -> dict[str, int]:
    """Write global rules to ~/.claude/rules/ directory.

    Returns counts of files written.
    """
    all_rules = get_active_rules(db)
    rules = [r for r in all_rules if not r.project_scope]

    if not rules:
        return {"files": 0}

    grouped = group_rules_by_category(rules)

    rules_dir = claude_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    files_written = 0

    for category, category_rules in grouped.items():
        filename = CATEGORY_TO_FILENAME.get(category, f"{category.lower().replace(' ', '-')}.md")
        file_path = rules_dir / filename

        if file_path.exists():
            existing = file_path.read_text()
            if "Auto-generated by claude-reinforcement" not in existing:
                continue

        content = format_rules_file_modern(category, category_rules)
        file_path.write_text(content)
        files_written += 1

    return {"files": files_written}


def update_all_rules_modern(
    db: Database,
    global_claude_dir: Path,
    project_paths: list[Path] | None = None,
) -> dict[str, int]:
    """Update all rule files using modern .claude/rules/ format.

    Returns counts of files updated.
    """
    counts = {"global_files": 0, "project_files": 0}

    # Update global rules
    result = write_global_rules_directory(db, global_claude_dir)
    counts["global_files"] = result["files"]

    # Update project-specific rules
    if project_paths:
        for project_path in project_paths:
            result = write_rules_directory(db, project_path)
            counts["project_files"] += result["files"]
    else:
        results = db.fetchall(
            "SELECT DISTINCT project_path FROM conversations"
        )
        for (project_path_str,) in results:
            project_path = Path(project_path_str)
            if project_path.exists():
                result = write_rules_directory(db, project_path)
                counts["project_files"] += result["files"]

    return counts
