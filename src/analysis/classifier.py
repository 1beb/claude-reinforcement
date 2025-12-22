"""Project and file type classification."""

from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

from src.db.database import Database


@dataclass
class ProjectClassification:
    """Classification result for a project."""

    project_path: str
    project_type: str
    confidence: float
    detected_at: str


# Default detection patterns
DEFAULT_PATTERNS: dict[str, list[str]] = {
    "django": ["manage.py", "**/settings.py", "**/urls.py", "**/wsgi.py"],
    "react": ["package.json", "src/App.jsx", "src/App.tsx", "src/App.js"],
    "vue": ["package.json", "src/App.vue", "vue.config.js"],
    "quarto": ["*.qmd", "_quarto.yml", "_quarto.yaml"],
    "r-package": ["DESCRIPTION", "NAMESPACE", "R/*.R"],
    "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"],
    "typescript": ["tsconfig.json"],
    "node": ["package.json"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
}

# Type hierarchy for inheritance
TYPE_HIERARCHY: dict[str, str | None] = {
    "django": "python",
    "flask": "python",
    "fastapi": "python",
    "react": "typescript",
    "vue": "typescript",
    "angular": "typescript",
    "typescript": "node",
    "node": None,
    "python": None,
    "r-package": None,
    "quarto": None,
    "rust": None,
    "go": None,
}

# File extension to type mapping
EXTENSION_TYPES: dict[str, str] = {
    ".py": "python",
    ".pyx": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "react",
    ".ts": "typescript",
    ".tsx": "react",
    ".vue": "vue",
    ".r": "r",
    ".R": "r",
    ".qmd": "quarto",
    ".rmd": "rmarkdown",
    ".md": "markdown",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".css": "css",
    ".scss": "scss",
    ".html": "html",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".xml": "xml",
}


def check_pattern(project_path: Path, pattern: str) -> bool:
    """Check if a pattern matches any file in the project."""
    # Handle glob patterns
    if "*" in pattern:
        return bool(list(project_path.glob(pattern)))

    # Handle exact file match
    target = project_path / pattern
    return target.exists()


def detect_project_type(
    project_path: Path,
    patterns: dict[str, list[str]] | None = None,
) -> ProjectClassification | None:
    """Detect the type of a project based on file patterns.

    Returns the most specific project type that matches.
    """
    if patterns is None:
        patterns = DEFAULT_PATTERNS

    if not project_path.exists():
        return None

    matches: list[tuple[str, int]] = []

    for project_type, type_patterns in patterns.items():
        match_count = 0
        for pattern in type_patterns:
            if check_pattern(project_path, pattern):
                match_count += 1

        if match_count > 0:
            matches.append((project_type, match_count))

    if not matches:
        return None

    # Sort by match count (more matches = more confident)
    matches.sort(key=lambda x: x[1], reverse=True)

    best_type, best_count = matches[0]

    # Calculate confidence based on match ratio
    total_patterns = len(patterns.get(best_type, []))
    confidence = best_count / total_patterns if total_patterns > 0 else 0.5

    return ProjectClassification(
        project_path=str(project_path),
        project_type=best_type,
        confidence=min(confidence, 1.0),
        detected_at=datetime.utcnow().isoformat(),
    )


def get_file_type(file_path: str | Path) -> str | None:
    """Get the type for a specific file based on extension."""
    if isinstance(file_path, str):
        file_path = Path(file_path)

    suffix = file_path.suffix.lower()

    # Handle .R vs .r (R is case-sensitive)
    if file_path.suffix == ".R":
        return "r"

    return EXTENSION_TYPES.get(suffix)


def get_parent_types(project_type: str) -> list[str]:
    """Get the inheritance chain for a project type.

    E.g., 'django' -> ['django', 'python']
    """
    types = [project_type]
    current = project_type

    while current in TYPE_HIERARCHY:
        parent = TYPE_HIERARCHY[current]
        if parent:
            types.append(parent)
            current = parent
        else:
            break

    return types


def save_classification(db: Database, classification: ProjectClassification) -> None:
    """Save or update a project classification."""
    db.execute(
        """
        INSERT OR REPLACE INTO project_classifications
        (project_path, project_type, detected_at, confidence)
        VALUES (?, ?, ?, ?)
        """,
        (
            classification.project_path,
            classification.project_type,
            classification.detected_at,
            classification.confidence,
        ),
    )


def get_classification(db: Database, project_path: str) -> ProjectClassification | None:
    """Get the classification for a project."""
    result = db.fetchone(
        """
        SELECT project_path, project_type, detected_at, confidence
        FROM project_classifications
        WHERE project_path = ?
        """,
        (project_path,),
    )

    if result:
        return ProjectClassification(
            project_path=result[0],
            project_type=result[1],
            detected_at=result[2],
            confidence=result[3],
        )

    return None


def classify_projects_from_conversations(db: Database) -> int:
    """Classify all projects that have conversations but no classification.

    Returns the number of projects classified.
    """
    # Get unique project paths from conversations that aren't classified
    results = db.fetchall(
        """
        SELECT DISTINCT c.project_path
        FROM conversations c
        LEFT JOIN project_classifications pc ON c.project_path = pc.project_path
        WHERE pc.project_path IS NULL
        """
    )

    classified = 0
    for (project_path,) in results:
        path = Path(project_path)
        if path.exists():
            classification = detect_project_type(path)
            if classification:
                save_classification(db, classification)
                classified += 1

    return classified
