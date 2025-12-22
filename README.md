# Claude Reinforcement

Learn from Claude Code conversations to improve CLAUDE.md instructions.

## The Guidance-Oversight Loop

Effective AI-assisted development requires two pillars ([Kieran Gill](https://blog.kierangill.xyz/oversight-and-guidance)):

- **Guidance**: Context provided to LLMs—conventions, patterns, constraints encoded in documentation like `CLAUDE.md`
- **Oversight**: Human expertise validating AI-generated choices—corrections, refinements, rejected suggestions

These pillars create a feedback loop. Good guidance reduces oversight burden. Oversight reveals guidance gaps. This project closes that loop automatically:

```
┌─────────────────────────────────────────────────────┐
│                                                     │
▼                                                     │
GUIDANCE (CLAUDE.md rules)                            │
    │                                                 │
    ▼                                                 │
Claude behavior in conversations                      │
    │                                                 │
    ▼                                                 │
OVERSIGHT (your corrections)                          │
    │                                                 │
    ▼                                                 │
claude-reinforcement extracts patterns ───────────────┘
```

Each correction you make during a conversation is evidence of a guidance gap. This tool detects those corrections, extracts preferences, and surfaces them for review. Approved rules update your `CLAUDE.md`, improving future guidance and reducing the need for repeated corrections.

The human review step is essential. Not all corrections should become permanent rules—some are context-specific, some need refinement, some need more evidence. The Obsidian review workflow preserves design judgment while automating pattern extraction.

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
