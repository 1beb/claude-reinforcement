"""Command-line interface for claude-reinforcement."""

import argparse
import logging
import sys
from pathlib import Path

from src.config import get_settings
from src.analysis.pipeline import run_pipeline


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Run the analysis pipeline."""
    config_path = Path(args.config) if args.config else None
    settings = get_settings(config_path)

    print("Claude Reinforcement - Analysis Pipeline")
    print("=" * 40)

    result = run_pipeline(settings)

    print("\nResults:")
    print(f"  Conversations: {result.conversations_new} new, {result.conversations_updated} updated")
    print(f"  Projects classified: {result.projects_classified}")
    print(f"  Corrections detected: {result.corrections_detected}")
    print(f"  Preferences extracted: {result.preferences_extracted}")
    print(f"  Reviews processed: {result.reviews_processed}")
    print(f"  Rules approved: {result.rules_approved}")
    print(f"  Obsidian files: {result.obsidian_files_written}")
    print(f"  CLAUDE.md updated: {result.claude_md_updated}")

    if result.errors:
        print("\nErrors:")
        for error in result.errors:
            print(f"  - {error}")
        return 1

    print("\nPipeline completed successfully!")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize the database."""
    from src.db.database import get_database

    config_path = Path(args.config) if args.config else None
    settings = get_settings(config_path)

    print(f"Initializing database at {settings.database.path}...")
    db = get_database(settings.database.path)
    print("Database initialized successfully!")

    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Show statistics."""
    from src.db.database import get_database

    config_path = Path(args.config) if args.config else None
    settings = get_settings(config_path)

    db = get_database(settings.database.path)

    conversations = db.fetchone("SELECT COUNT(*) FROM conversations")[0]
    messages = db.fetchone("SELECT COUNT(*) FROM messages")[0]
    corrections = db.fetchone("SELECT COUNT(*) FROM corrections")[0]
    preferences = db.fetchone("SELECT COUNT(*) FROM file_type_preferences")[0]
    rules = db.fetchone("SELECT COUNT(*) FROM learned_rules WHERE active = 1")[0]
    pending = db.fetchone("SELECT COUNT(*) FROM review_queue WHERE status = 'pending'")[0]

    print("Claude Reinforcement - Statistics")
    print("=" * 40)
    print(f"  Conversations: {conversations}")
    print(f"  Messages: {messages}")
    print(f"  Corrections detected: {corrections}")
    print(f"  Preferences: {preferences}")
    print(f"  Active rules: {rules}")
    print(f"  Pending reviews: {pending}")

    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract preferences using LLM."""
    from src.db.database import get_database
    from src.analysis.llm_extractor import (
        extract_preferences_from_db,
        save_extracted_preferences,
    )

    config_path = Path(args.config) if args.config else None
    settings = get_settings(config_path)

    db = get_database(settings.database.path)

    provider = args.provider
    batch_size = args.batch_size
    limit = args.limit if args.limit > 0 else None

    print(f"Claude Reinforcement - LLM Preference Extraction")
    print("=" * 40)
    print(f"  Provider: {provider}")
    print(f"  Batch size: {batch_size}")
    print(f"  Limit: {limit or 'all'}")
    print()

    preferences = extract_preferences_from_db(
        db,
        provider=provider,
        batch_size=batch_size,
        limit=limit,
    )

    print(f"\nExtracted {len(preferences)} preferences:")
    for pref in preferences:
        print(f"  [{pref.preference_type}] {pref.preference_text} (confidence: {pref.confidence:.2f})")

    if preferences and not args.dry_run:
        saved = save_extracted_preferences(db, preferences)
        print(f"\nSaved {saved} preferences to review queue.")
    elif args.dry_run:
        print("\n(Dry run - not saved)")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Claude Reinforcement - Learn from Claude Code conversations"
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to configuration file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run the analysis pipeline")

    # Init command
    init_parser = subparsers.add_parser("init", help="Initialize the database")

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")

    # Extract command (LLM-based)
    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract preferences using LLM"
    )
    extract_parser.add_argument(
        "-p", "--provider",
        choices=["anthropic", "openai"],
        default="anthropic",
        help="LLM provider to use (default: anthropic)",
    )
    extract_parser.add_argument(
        "-b", "--batch-size",
        type=int,
        default=20,
        help="Messages per API call (default: 20)",
    )
    extract_parser.add_argument(
        "-l", "--limit",
        type=int,
        default=0,
        help="Max messages to process (default: 0 = all)",
    )
    extract_parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Don't save results to database",
    )

    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.command == "run":
        return cmd_run(args)
    elif args.command == "init":
        return cmd_init(args)
    elif args.command == "stats":
        return cmd_stats(args)
    elif args.command == "extract":
        return cmd_extract(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
