# Reference Repositories

These repositories were analyzed during the design phase of claude-reinforcement. They provided inspiration, patterns, and architectural insights.

## Repositories Analyzed

### 1. @modelcontextprotocol/server-memory (Official)

**URL:** https://github.com/modelcontextprotocol/servers/tree/main/src/memory
**Tech:** TypeScript, JSON/JSONL file storage
**Local copy:** `mcp-servers-official/src/memory/`

**Key insights:**
- Simple knowledge graph model (entities, relations, observations)
- Clean Zod schema validation
- JSONL storage format for append-friendly persistence

**Borrowed patterns:**
- Entity/relation/observation conceptual model
- Tool schema structure with Zod

---

### 2. mcp-memory-service (doobidoo)

**URL:** https://github.com/doobidoo/mcp-memory-service
**Tech:** Python, SQLite-vec, Cloudflare D1, ONNX embeddings
**Local copy:** `mcp-memory-service/`

**Key insights:**
- Dream-inspired consolidation (decay, associations, clustering, compression, forgetting)
- Multi-backend storage abstraction (local + cloud hybrid)
- Quality scoring with implicit signals (access count, recency)
- 30+ MCP tools

**Borrowed patterns:**
- Confidence/decay scoring algorithm
- Quality-based retention policies
- Hybrid sync architecture concept

---

### 3. rag-memory-mcp

**URL:** https://github.com/ttommyth/rag-memory-mcp
**Tech:** TypeScript, SQLite + sqlite-vec, sentence-transformers
**Local copy:** `rag-memory-mcp/`

**Key insights:**
- Hybrid vector + knowledge graph search
- Document chunking with configurable overlap
- Semantic summarization with sentence-level scoring
- Sophisticated fallback embeddings (n-gram hash-based)
- Knowledge graph nodes embedded alongside document chunks

**Borrowed patterns:**
- sqlite-vec integration approach
- Hybrid search architecture
- Tool registry pattern with Zod-to-MCP conversion

---

### 4. mcp-memory-keeper

**URL:** https://github.com/mkreyman/mcp-memory-keeper
**Tech:** TypeScript, SQLite with WAL mode
**Local copy:** `mcp-memory-keeper/`

**Key insights:**
- 40 MCP tools for comprehensive context management
- Session/checkpoint system for compaction survival
- Git integration (branch tracking, status snapshots)
- Channel-based organization derived from git branches
- N-gram embeddings without ML dependencies

**Borrowed patterns:**
- Session management concepts
- Checkpoint/restore for context preservation
- Git-aware project tracking

---

### 5. Claude-CursorMemoryMCP

**URL:** https://github.com/Angleito/Claude-CursorMemoryMCP
**Tech:** Python, PostgreSQL + pgvector, FastAPI, Redis
**Local copy:** `Claude-CursorMemoryMCP/`

**Key insights:**
- Production-grade architecture with connection pooling
- Multi-provider embedding abstraction (OpenAI, Cohere, local)
- Vector compression (PCA, quantization)
- Memory deduplication strategies
- Comprehensive Pydantic validation

**Borrowed patterns:**
- FastAPI project structure
- Pydantic settings configuration
- Embedding provider abstraction concept

---

## Feature Comparison Matrix

| Feature | server-memory | mcp-memory-service | rag-memory-mcp | mcp-memory-keeper | Claude-CursorMemoryMCP |
|---------|--------------|-------------------|----------------|-------------------|----------------------|
| **Storage** | JSONL file | SQLite-vec + D1 | SQLite + vec | SQLite WAL | PostgreSQL + pgvector |
| **Vector search** | No | Yes | Yes | N-gram only | Yes |
| **Knowledge graph** | Yes | Partial | Yes | Yes | No |
| **Multi-device** | No | Yes (Cloudflare) | No | No | No |
| **Decay/retention** | No | Yes | No | Yes | Yes (TTL) |
| **Git integration** | No | No | No | Yes | No |
| **Document RAG** | No | Yes | Yes | No | No |
| **MCP tools** | 8 | 30+ | 23 | 40 | 6 |

## What claude-reinforcement Takes From Each

| Source | What We Use |
|--------|-------------|
| **server-memory** | Entity/relation model simplicity |
| **mcp-memory-service** | Decay scoring, quality thresholds, hybrid sync concept |
| **rag-memory-mcp** | sqlite-vec setup, hybrid search, tool schemas |
| **mcp-memory-keeper** | Git awareness, session concepts, checkpoint patterns |
| **Claude-CursorMemoryMCP** | FastAPI structure, Pydantic patterns |

## What claude-reinforcement Does Differently

1. **Correction extraction** - None of these analyze conversations for corrections/preferences
2. **File-type awareness** - Rules scoped to specific file extensions
3. **Obsidian integration** - Review queue as markdown with checkboxes
4. **CLAUDE.md generation** - Direct output to Claude Code's native instruction format
5. **Human-in-the-loop** - Explicit approval workflow before rules activate
