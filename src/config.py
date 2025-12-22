"""Configuration management using Pydantic Settings."""

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml


class ServerConfig(BaseSettings):
    """Server configuration."""

    host: str = "0.0.0.0"
    port: int = 8420


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    path: Path = Path("/data/claude-reinforcement.db")


class ObsidianConfig(BaseSettings):
    """Obsidian vault configuration."""

    vault_path: Path = Path("~/pCloudDrive/Personal/Obsidian/personal").expanduser()
    folder: str = "AI-improvement"

    @property
    def output_path(self) -> Path:
        """Full path to the AI-improvement folder."""
        return self.vault_path / self.folder


class SyncConfig(BaseSettings):
    """Sync configuration."""

    claude_projects_path: Path = Path("~/.claude/projects").expanduser()


class AnalysisConfig(BaseSettings):
    """Analysis pipeline configuration."""

    auto_approve_threshold: float = 0.85
    review_threshold: float = 0.5
    batch_schedule: str = "0 3 * * *"  # 3am daily


class DeviceConfig(BaseSettings):
    """Device configuration."""

    name: str
    id: str


class ProjectDetectionConfig(BaseSettings):
    """Project type detection patterns."""

    patterns: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "django": ["manage.py", "settings.py", "**/urls.py"],
            "react": ['package.json + "react"', "src/App.{jsx,tsx}"],
            "quarto": ["*.qmd", "_quarto.yml"],
            "r-package": ["DESCRIPTION", "NAMESPACE", "R/*.R"],
            "python": ["pyproject.toml", "setup.py", "requirements.txt"],
            "typescript": ["tsconfig.json"],
        }
    )


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_prefix="CLAUDE_REINFORCEMENT_",
        env_nested_delimiter="__",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    project_detection: ProjectDetectionConfig = Field(default_factory=ProjectDetectionConfig)
    devices: list[DeviceConfig] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "Settings":
        """Load settings from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)


def get_settings(config_path: Path | None = None) -> Settings:
    """Get application settings."""
    if config_path and config_path.exists():
        return Settings.from_yaml(config_path)
    return Settings()
