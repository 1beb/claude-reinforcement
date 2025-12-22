# Claude Reinforcement - Design Document

**Date:** 2025-12-22
**Status:** Approved for implementation
**Project Name:** claude-reinforcement

## Overview

A self-hosted system that learns from Claude Code conversations across devices, extracts corrections and preferences, and automatically updates CLAUDE.md instruction files. Integrates with Obsidian for human review of proposed rules.

## Goals

1. **Learn from corrections** - Auto-extract rules from explicit corrections, refinements, and silent edits
2. **File-type aware preferences** - Different rules for .qmd, .R, .ts, .py, etc.
3. **Cross-device sync** - Aggregate conversations from multiple machines
4. **Human review workflow** - Obsidian-based review queue with checkbox decisions
5. **Continuous improvement** - Claude gets better unattended over time

## Architecture

### High-Level

```
┌─────────────────────────────────────────────────────────────────┐
│                     Home Server (Docker)                         │
│                                                                  │
│  ┌─────────────────┐  ┌────────────────────┐                    │
│  │  Daily Analyzer │  │    MCP Server      │                    │
│  │                 │  │    (Phase 2)       │                    │
│  │ - Ingest convos │  │                    │                    │
│  │ - Detect correct│  │ search_memory()    │                    │
│  │ - Extract prefs │  │ get_context()      │                    │
│  │ - Generate rules│  │ why_this_rule()    │                    │
│  │ - Update .md    │  └─────────┬──────────┘                    │
│  └────────┬────────┘            │                               │
│           │                     │                               │
│           └──────────┬──────────┘                               │
│                      │                                          │
│               ┌──────▼──────┐                                   │
│               │   SQLite    │                                   │
│               │  + vec ext  │                                   │
│               └─────────────┘                                   │
└─────────────────────────────────────────────────────────────────┘
         ▲                                      │
         │ pCloud sync                          │ writes
         │                                      ▼
    ~/.claude/projects/                  Obsidian vault
    (from all devices)                   AI-improvement/
```

### Hybrid Approach

- **Batch process (primary)**: Daily analysis, writes to CLAUDE.md files
- **MCP server (Phase 2)**: Real-time memory search during sessions

## Data Model

```sql
-- Core conversation storage
conversations (
  id              TEXT PRIMARY KEY,
  device_id       TEXT NOT NULL,
  project_path    TEXT NOT NULL,
  session_id      TEXT NOT NULL,
  started_at      DATETIME,
  ended_at        DATETIME,
  git_branch      TEXT,
  synced_at       DATETIME
)

messages (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations,
  role            TEXT NOT NULL,       -- 'user' | 'assistant'
  content         TEXT NOT NULL,
  timestamp       DATETIME,
  embedding       FLOAT[384],
  parent_uuid     TEXT
)

-- Extracted insights
corrections (
  id              TEXT PRIMARY KEY,
  message_id      TEXT REFERENCES messages,
  target_msg_id   TEXT REFERENCES messages,
  correction_type TEXT,                -- 'explicit' | 'refinement' | 'silent_fix'
  extracted_rule  TEXT,
  confidence      FLOAT,
  reviewed        BOOLEAN DEFAULT FALSE,
  approved        BOOLEAN
)

-- File-type preferences
file_type_preferences (
  id              TEXT PRIMARY KEY,
  file_extension  TEXT NOT NULL,
  category        TEXT NOT NULL,
  preference_key  TEXT NOT NULL,
  preference_value TEXT NOT NULL,
  evidence        TEXT,                -- JSON array
  occurrence_count INTEGER DEFAULT 1,
  confidence      FLOAT,
  first_seen      DATETIME,
  last_seen       DATETIME,
  UNIQUE(file_extension, preference_key)
)

-- Project type detection
project_types (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  detection_rules TEXT,                -- JSON
  parent_type     TEXT
)

project_classifications (
  project_path    TEXT PRIMARY KEY,
  project_type    TEXT REFERENCES project_types,
  detected_at     DATETIME,
  confidence      FLOAT
)

-- Learned rules
learned_rules (
  id              TEXT PRIMARY KEY,
  rule_text       TEXT NOT NULL,
  source          TEXT,                -- 'correction' | 'preference' | 'manual'
  project_scope   TEXT,                -- NULL = global
  project_type    TEXT,                -- NULL = any
  file_types      TEXT,                -- JSON array
  active          BOOLEAN DEFAULT TRUE,
  created_at      DATETIME,
  approved_at     DATETIME
)

-- Review queue
review_queue (
  id              TEXT PRIMARY KEY,
  rule_type       TEXT NOT NULL,
  proposed_rule   TEXT NOT NULL,
  file_types      TEXT,
  project_scope   TEXT,
  confidence      FLOAT,
  status          TEXT DEFAULT 'pending',
  created_at      DATETIME,
  reviewed_at     DATETIME
)

review_evidence (
  id              TEXT PRIMARY KEY,
  review_id       TEXT REFERENCES review_queue,
  conversation_id TEXT REFERENCES conversations,
  project_path    TEXT,
  timestamp       DATETIME,
  context_before  TEXT,
  trigger_message TEXT,
  context_after   TEXT,
  file_touched    TEXT,
  evidence_type   TEXT
)

-- Document integration
documents (
  id              TEXT PRIMARY KEY,
  file_path       TEXT NOT NULL,
  project_path    TEXT,
  content         TEXT,
  content_hash    TEXT,
  last_synced     DATETIME
)

document_chunks (
  id              TEXT PRIMARY KEY,
  document_id     TEXT REFERENCES documents,
  chunk_text      TEXT,
  embedding       FLOAT[384],
  chunk_index     INTEGER
)

-- Track edits for silent fix detection
file_edits (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations,
  file_path       TEXT NOT NULL,
  file_extension  TEXT NOT NULL,
  claude_wrote    TEXT,
  final_version   TEXT,
  diff_summary    TEXT,
  timestamp       DATETIME
)
```

## Analysis Pipeline

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│ 1. Ingest    │───▶│ 2. Classify  │───▶│ 3. Extract       │
│              │    │              │    │                  │
│ - New convos │    │ - Project    │    │ - Corrections    │
│ - Git diffs  │    │   type       │    │ - Preferences    │
│ - Doc changes│    │ - File types │    │ - Patterns       │
└──────────────┘    └──────────────┘    └──────────────────┘
                                               │
                                               ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│ 6. Write     │◀───│ 5. Generate  │◀───│ 4. Score &       │
│              │    │              │    │    Merge         │
│ - CLAUDE.md  │    │ - Rule text  │    │                  │
│ - Per project│    │ - By scope   │    │ - Confidence     │
│ - By type    │    │ - By type    │    │ - Dedup          │
└──────────────┘    └──────────────┘    └──────────────────┘
```

### Correction Detection Patterns

```python
CORRECTION_SIGNALS = [
    r"(?i)^no[,.]?\s",                    # "No, do it this way"
    r"(?i)don'?t\s+\w+",                  # "Don't use X"
    r"(?i)instead\s+of",                  # "Instead of X, use Y"
    r"(?i)never\s+\w+",                   # "Never do X"
    r"(?i)always\s+\w+",                  # "Always do X"
    r"(?i)i\s+prefer",                    # "I prefer X"
    r"(?i)please\s+(always|never)",       # "Please always X"
    r"(?i)use\s+\w+\s+instead",           # "Use X instead"
    r"(?i)make\s+it\s+(more|less)",       # "Make it more concise"
    r"(?i)simpler",
    r"(?i)too\s+(verbose|complex|long)",
]

POSITIVE_SIGNALS = [
    r"(?i)^(perfect|great|thanks|good)",
    r"(?i)that'?s?\s+(right|correct)",
]
```

### Confidence Scoring

```python
def calculate_confidence(preference):
    base = 0.3
    base += min(preference.occurrence_count * 0.1, 0.4)  # Repetition
    if preference.source == 'explicit_correction':
        base += 0.2
    days_old = (now - preference.last_seen).days
    if days_old < 7:
        base += 0.1  # Recency
    if has_conflicting_preference(preference):
        base -= 0.3
    return min(base, 1.0)
```

**Thresholds:**
- 0.85+ → Auto-approve, add to CLAUDE.md
- 0.5-0.85 → Review queue
- <0.5 → Keep collecting evidence

## Obsidian Integration

**Vault:** `~/pCloudDrive/Personal/Obsidian/personal`
**Folder:** `AI-improvement/`

### Folder Structure

```
AI-improvement/
├── reviews/
│   ├── 2025-12-22-pending.md
│   └── ...
├── digests/
│   ├── 2025-12-22-digest.md
│   └── 2025-W51-digest.md
├── archive/
│   └── 2025-12/
├── rules/
│   ├── global.md
│   ├── by-type/
│   │   ├── qmd.md
│   │   ├── R.md
│   │   └── py.md
│   └── by-project/
│       └── rental-app.md
└── index.md
```

### Review Format

```markdown
## Rule 1: QMD Render on Completion
**Confidence:** 0.72 | **File types:** `qmd` | **Scope:** Global

> When working with QMD documents, always attempt to render as the last step.

### Evidence (3 occurrences)

#### panel_police - Dec 15
> **You:** Update the analysis section...
> **Claude:** I've updated the analysis section with...
> **You:** ==Your last step should always be to render the document==

### Decision
- [x] Approve as written
- [ ] Approve with edits: `___`
- [ ] Reject (reason: ___)
- [ ] Need more evidence
```

## Tech Stack

```yaml
Language: Python 3.11+
API: FastAPI + Uvicorn
Database: SQLite + sqlite-vec
Embeddings: sentence-transformers (all-MiniLM-L6-v2)
Scheduling: APScheduler or systemd timer
MCP: mcp-python-sdk (Phase 2)
Config: Pydantic Settings + YAML
Docker: Multi-stage build, single container
```

## Project Structure

```
claude-reinforcement/
├── src/
│   ├── api/
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── sync.py
│   │   │   ├── rules.py
│   │   │   └── health.py
│   │   └── models.py
│   │
│   ├── analysis/
│   │   ├── pipeline.py
│   │   ├── ingest.py
│   │   ├── classifier.py
│   │   ├── corrections.py
│   │   ├── preferences.py
│   │   ├── scoring.py
│   │   └── embeddings.py
│   │
│   ├── generators/
│   │   ├── rules.py
│   │   ├── claude_md.py
│   │   └── obsidian.py
│   │
│   ├── mcp/                     # Phase 2
│   │   ├── server.py
│   │   └── tools.py
│   │
│   ├── db/
│   │   ├── database.py
│   │   ├── models.py
│   │   └── migrations/
│   │
│   └── config.py
│
├── scripts/
│   └── run-analysis.py
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── config/
│   └── config.example.yaml
│
├── tests/
├── pyproject.toml
└── README.md
```

## Configuration

```yaml
server:
  host: 0.0.0.0
  port: 8420

database:
  path: /data/claude-reinforcement.db

obsidian:
  vault_path: ~/pCloudDrive/Personal/Obsidian/personal
  folder: AI-improvement

sync:
  claude_projects_path: /claude-data  # pCloud mounted

analysis:
  auto_approve_threshold: 0.85
  review_threshold: 0.5
  batch_schedule: "0 3 * * *"  # 3am daily

project_detection:
  django: ['manage.py', 'settings.py', '**/urls.py']
  react: ['package.json + "react"', 'src/App.{jsx,tsx}']
  quarto: ['*.qmd', '_quarto.yml']
  r-package: ['DESCRIPTION', 'NAMESPACE', 'R/*.R']
  python: ['pyproject.toml', 'setup.py', 'requirements.txt']
  typescript: ['tsconfig.json']

devices:
  - name: desktop
    id: desktop-main
  - name: laptop
    id: laptop-work
```

## Docker Deployment

**docker-compose.yml:**
```yaml
version: '3.8'

services:
  claude-reinforcement:
    build: .
    container_name: claude-reinforcement
    restart: unless-stopped
    ports:
      - "8420:8420"
    volumes:
      - ./data:/data
      - ./config:/config
      - ~/pCloudDrive/Personal/Obsidian/personal/AI-improvement:/obsidian
      - /path/to/synced/claude-projects:/claude-data:ro
    environment:
      - CONFIG_PATH=/config/config.yaml
```

## MCP Tools (Phase 2)

```python
# Real-time memory queries
search_memory(query: str, scope: str = 'all')
get_project_rules(project_path: str = None)
get_preferences(category: str = None, project_path: str = None)
get_related_conversations(query: str, limit: int = 5)
why_this_rule(rule_id: str)  # Show evidence
record_correction(assistant_msg_id: str, user_correction: str)
suggest_rule(rule_text: str, reason: str)
```

## Roadmap

### Phase 1 (MVP)

- [x] Design complete
- [ ] Project scaffolding
- [ ] SQLite + sqlite-vec setup
- [ ] Conversation ingestion (parse JSONL)
- [ ] Project/file type classification
- [ ] Correction detection
- [ ] Preference extraction
- [ ] Confidence scoring
- [ ] Obsidian review queue generation
- [ ] Review decision processing
- [ ] CLAUDE.md generation
- [ ] Docker deployment
- [ ] Documentation

### Phase 2 (MCP)

- [ ] MCP server implementation
- [ ] search_memory() tool
- [ ] get_context() tool
- [ ] why_this_rule() tool
- [ ] Claude Code integration

### Future Features

- [ ] REST API sync (alternative to pCloud)
- [ ] Git diff analysis (silent edit detection)
- [ ] Usage/cost tracking integration
- [ ] Web dashboard for review queue
- [ ] Weekly/monthly trend reports
- [ ] Rule conflict detection
- [ ] Preference decay
- [ ] Export rules for teams
