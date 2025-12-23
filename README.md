# Claude Reinforcement

Learn from Claude Code conversations to improve CLAUDE.md instructions automatically.

## The Guidance-Oversight Loop

Every time you correct Claude, you're revealing a gap in its instructions. This tool closes that loop:

```
GUIDANCE (CLAUDE.md rules)
    │
    ▼
Claude behavior in conversations
    │
    ▼
OVERSIGHT (your corrections: "no", "don't", "always use X")
    │
    ▼
claude-reinforcement extracts patterns
    │
    ▼
Human review in Obsidian
    │
    ▼
Approved rules → CLAUDE.md ───────────────────┐
                                              │
    ┌─────────────────────────────────────────┘
    │
    ▼
Fewer corrections needed next time
```

## Quick Start

```bash
# Install
uv sync

# Configure
cp config/config.example.yaml config/config.yaml
# Edit paths: claude_projects_path, obsidian vault_path

# Initialize database
uv run python -m src.cli init

# Run analysis
uv run python -m src.cli run
```

## How to Use

### 1. Run the Pipeline

```bash
uv run python -m src.cli run
```

This:
- Ingests new conversations from `~/.claude/projects/`
- Detects corrections ("don't use git add .", "always render qmd files")
- Extracts preferences (file-type specific patterns, tool choices)
- Scores confidence based on repetition and explicitness
- Generates Obsidian review notes for items needing human judgment

### 2. Review in Obsidian

Open your vault, navigate to `AI-improvement/reviews/`. Each pending rule shows:

```markdown
## Rule: Never use git add .
**Confidence:** 0.72 | **File types:** global

> Always specify files explicitly when committing

### Evidence (2 occurrences)

#### rental-app - Dec 20
> You: Don't use git add . - specify files explicitly

### Decision
- [ ] Approve as written
- [ ] Approve with edits: `___`
- [ ] Reject (reason: ___)
- [ ] Need more evidence
```

Check one box and save. Next pipeline run processes your decisions.

### 3. Rules Get Written

Approved rules are written to:
- `~/.claude/CLAUDE.md` - global rules
- `project/.claude/rules/*.md` - project-specific rules (modern format)
- `project/.claude/CLAUDE.md` - legacy format

The system respects manually-written content. Auto-generated sections are marked and only those get updated.

### 4. Repeat

Run periodically (daily/weekly). Over time:
- Claude makes fewer mistakes you've corrected before
- Your correction burden decreases
- Instructions stay current with your evolving preferences

## What Gets Detected

**Explicit corrections:**
- "No, do it this way"
- "Don't use X"
- "Always do Y"
- "Use X instead of Y"

**Preferences:**
- Tool choices: "use uv not pip", "prefer pnpm"
- Code style: "use |> pipe in R", "no emojis in markdown"
- Workflow: "run tests before committing"
- Communication: "be more concise"

**File-type awareness:**
- Rules can be scoped to `.qmd`, `.py`, `.ts`, etc.
- Different projects can have different rules

## Confidence Scoring

Not everything you say should become a permanent rule. The system scores confidence:

| Factor | Impact |
|--------|--------|
| Repetition (said 3+ times) | +0.3 |
| Explicit correction language | +0.2 |
| Recent (last 7 days) | +0.1 |
| Conflicting preferences | -0.3 |

**Thresholds:**
- 0.85+ → Can auto-approve (if enabled)
- 0.5-0.85 → Review queue
- <0.5 → Keep collecting evidence

## Pipeline Steps

```
1. Ingest      → Parse conversation JSONL files
2. Classify    → Detect project types (django, react, quarto, etc.)
3. Corrections → Find "no", "don't", "always" patterns
4. Preferences → Extract tool/style preferences
5. Review      → Process Obsidian checkbox decisions
6. Obsidian    → Generate new review notes
7. CLAUDE.md   → Write approved rules (legacy format)
8. Rules/      → Write .claude/rules/*.md (modern format)
9. Skills      → Generate workflow skills (experimental)
```

## Configuration

```yaml
# config/config.yaml

database:
  path: ./data/claude-reinforcement.db

obsidian:
  vault_path: ~/path/to/obsidian/vault
  folder: AI-improvement

sync:
  claude_projects_path: ~/.claude/projects

analysis:
  auto_approve_threshold: 0.85  # Rules above this skip review
  review_threshold: 0.5         # Rules below this need more evidence

devices:
  - name: desktop
    id: desktop-main
```

## Output Formats

### Modern: `.claude/rules/` (Recommended)

Rules organized by category with YAML frontmatter for file-type targeting:

```markdown
---
paths: ["**/*.py"]
---

# Code Style Rules

- Use |> pipe operator in R dplyr chains
- Never use git add . - specify files explicitly
```

### Legacy: `CLAUDE.md`

Rules appended between markers:

```markdown
<!-- BEGIN CLAUDE-REINFORCEMENT -->
## Workflow
- Run tests before committing
<!-- END CLAUDE-REINFORCEMENT -->
```

Manual content outside markers is preserved.

## CLI Commands

```bash
# Initialize database
uv run python -m src.cli init

# Run full pipeline
uv run python -m src.cli run

# View statistics
uv run python -m src.cli stats

# Use specific config
uv run python -m src.cli run -c config/production.yaml
```

## Project Status

### Working

- Conversation ingestion from Claude Code history
- Correction and preference detection
- Confidence scoring with repetition/recency factors
- Obsidian review queue with checkbox workflow
- Rule generation for CLAUDE.md
- Modern `.claude/rules/` output with YAML frontmatter
- File-type and project-scope awareness
- LLM-powered conversation summarization for better extraction

### In Progress

- Skill generation from workflow patterns
- Rule deduplication (semantic similarity)

### Planned

- MCP server for real-time memory queries
- Git diff analysis (detect silent edits)
- Web dashboard alternative to Obsidian
- Weekly trend reports

## Reference: CLAUDE.md Best Practices

Claude Code uses a hierarchical memory system:

| Level | Location | Purpose |
|-------|----------|---------|
| Project | `./CLAUDE.md` | Team-shared, committed to git |
| Project rules | `./.claude/rules/*.md` | Modular topic-specific rules |
| User | `~/.claude/CLAUDE.md` | Personal preferences (all projects) |
| Local | `./CLAUDE.local.md` | Personal project-specific (gitignored) |

Rules can be conditionally applied:

```markdown
---
paths: ["*.tsx", "*.jsx"]
---
# React Guidelines
- Use functional components
```

For larger projects, keep main CLAUDE.md under 200 lines and extract detailed sections to `.claude/rules/`.

**Sources:**
- [Claude Code Memory Docs](https://code.claude.com/docs/en/memory)
- [Claude Code Best Practices](https://www.anthropic.com/engineering/claude-code-best-practices)
