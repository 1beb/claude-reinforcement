# Claude Reinforcement

Learn from Claude Code conversations to improve CLAUDE.md instructions.

## Quick Start

```bash
# Install dependencies
uv sync

# Create config
cp config/config.example.yaml config/config.yaml
# Edit config.yaml with your paths

# Initialize database
uv run python -m src.cli init -c config/config.yaml

# Run analysis pipeline
uv run python -m src.cli run -c config/config.yaml

# View stats
uv run python -m src.cli stats -c config/config.yaml
```

## Features

- Ingests Claude Code conversation history
- Detects corrections and preferences from your feedback
- Generates Obsidian review notes with checkbox decisions
- Updates CLAUDE.md files with approved rules
- File-type aware learning (different rules for .qmd, .py, .ts, etc.)
