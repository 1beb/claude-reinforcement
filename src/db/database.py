"""SQLite database with sqlite-vec for vector operations."""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator
import json

import apsw
import sqlite_vec


class Database:
    """SQLite database manager with vector search support."""

    def __init__(self, db_path: Path):
        """Initialize database connection."""
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: apsw.Connection | None = None

    @property
    def connection(self) -> apsw.Connection:
        """Get or create database connection."""
        if self._connection is None:
            self._connection = apsw.Connection(str(self.db_path))
            self._connection.enable_load_extension(True)
            sqlite_vec.load(self._connection)
            self._connection.enable_load_extension(False)
            self._setup_pragmas()
        return self._connection

    def _setup_pragmas(self) -> None:
        """Set up SQLite pragmas for performance."""
        cursor = self.connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=10000")
        cursor.execute("PRAGMA busy_timeout=5000")

    @contextmanager
    def cursor(self) -> Generator[apsw.Cursor, None, None]:
        """Context manager for database cursor."""
        cursor = self.connection.cursor()
        try:
            yield cursor
        finally:
            pass  # apsw cursors don't need explicit close

    @contextmanager
    def transaction(self) -> Generator[apsw.Cursor, None, None]:
        """Context manager for database transaction."""
        cursor = self.connection.cursor()
        cursor.execute("BEGIN")
        try:
            yield cursor
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> apsw.Cursor:
        """Execute a SQL statement."""
        cursor = self.connection.cursor()
        if params:
            return cursor.execute(sql, params)
        return cursor.execute(sql)

    def executemany(self, sql: str, params: list[tuple[Any, ...]]) -> None:
        """Execute a SQL statement with multiple parameter sets."""
        cursor = self.connection.cursor()
        cursor.executemany(sql, params)

    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> tuple[Any, ...] | None:
        """Execute and fetch one result."""
        cursor = self.execute(sql, params)
        return cursor.fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[tuple[Any, ...]]:
        """Execute and fetch all results."""
        cursor = self.execute(sql, params)
        return cursor.fetchall()

    def init_schema(self) -> None:
        """Initialize database schema."""
        with self.transaction() as cursor:
            # Conversations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    git_branch TEXT,
                    synced_at TEXT
                )
            """)

            # Messages table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT,
                    parent_uuid TEXT,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                )
            """)

            # Message embeddings virtual table (384-dim for all-MiniLM-L6-v2)
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS message_embeddings USING vec0(
                    message_id TEXT PRIMARY KEY,
                    embedding FLOAT[384]
                )
            """)

            # Project classifications
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS project_classifications (
                    project_path TEXT PRIMARY KEY,
                    project_type TEXT NOT NULL,
                    detected_at TEXT,
                    confidence REAL
                )
            """)

            # File type preferences
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS file_type_preferences (
                    id TEXT PRIMARY KEY,
                    file_extension TEXT NOT NULL,
                    category TEXT NOT NULL,
                    preference_key TEXT NOT NULL,
                    preference_value TEXT NOT NULL,
                    evidence TEXT,
                    occurrence_count INTEGER DEFAULT 1,
                    confidence REAL,
                    first_seen TEXT,
                    last_seen TEXT,
                    UNIQUE(file_extension, preference_key)
                )
            """)

            # Corrections
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS corrections (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL REFERENCES messages(id),
                    target_msg_id TEXT REFERENCES messages(id),
                    correction_type TEXT,
                    extracted_rule TEXT,
                    confidence REAL,
                    reviewed INTEGER DEFAULT 0,
                    approved INTEGER,
                    FOREIGN KEY (message_id) REFERENCES messages(id)
                )
            """)

            # Learned rules
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS learned_rules (
                    id TEXT PRIMARY KEY,
                    rule_text TEXT NOT NULL,
                    source TEXT,
                    project_scope TEXT,
                    project_type TEXT,
                    file_types TEXT,
                    active INTEGER DEFAULT 1,
                    created_at TEXT,
                    approved_at TEXT
                )
            """)

            # Review queue
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS review_queue (
                    id TEXT PRIMARY KEY,
                    rule_type TEXT NOT NULL,
                    proposed_rule TEXT NOT NULL,
                    file_types TEXT,
                    project_scope TEXT,
                    confidence REAL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    reviewed_at TEXT
                )
            """)

            # Review evidence
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS review_evidence (
                    id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL REFERENCES review_queue(id),
                    conversation_id TEXT REFERENCES conversations(id),
                    project_path TEXT,
                    timestamp TEXT,
                    context_before TEXT,
                    trigger_message TEXT,
                    context_after TEXT,
                    file_touched TEXT,
                    evidence_type TEXT,
                    FOREIGN KEY (review_id) REFERENCES review_queue(id)
                )
            """)

            # Documents (for Obsidian/docs integration)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    project_path TEXT,
                    content TEXT,
                    content_hash TEXT,
                    last_synced TEXT
                )
            """)

            # Document chunks with embeddings
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks USING vec0(
                    id TEXT PRIMARY KEY,
                    document_id TEXT,
                    chunk_text TEXT,
                    chunk_index INTEGER,
                    embedding FLOAT[384]
                )
            """)

            # File edits for silent fix detection
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS file_edits (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT REFERENCES conversations(id),
                    file_path TEXT NOT NULL,
                    file_extension TEXT NOT NULL,
                    claude_wrote TEXT,
                    final_version TEXT,
                    diff_summary TEXT,
                    timestamp TEXT,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                )
            """)

            # Create indexes
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
                "ON messages(conversation_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_timestamp "
                "ON messages(timestamp)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_project "
                "ON conversations(project_path)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_queue_status "
                "ON review_queue(status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_learned_rules_active "
                "ON learned_rules(active)"
            )

    def close(self) -> None:
        """Close database connection."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None


# Global database instance
_database: Database | None = None


def get_database(db_path: Path | None = None) -> Database:
    """Get or create the global database instance."""
    global _database
    if _database is None:
        if db_path is None:
            raise ValueError("db_path required for initial database creation")
        _database = Database(db_path)
        _database.init_schema()
    return _database
