"""Main analysis pipeline orchestrator."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging

from src.config import Settings
from src.db.database import Database, get_database
from src.analysis.ingest import ingest_all_conversations
from src.analysis.classifier import classify_projects_from_conversations
from src.analysis.corrections import detect_all_corrections, save_correction
from src.analysis.preferences import process_corrections_to_preferences
from src.generators.obsidian import write_obsidian_notes
from src.generators.review_processor import process_review_files, add_to_review_queue
from src.generators.claude_md import update_all_claude_md_files


logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of running the analysis pipeline."""

    started_at: str
    completed_at: str
    conversations_new: int
    conversations_updated: int
    projects_classified: int
    corrections_detected: int
    preferences_extracted: int
    reviews_processed: int
    rules_approved: int
    obsidian_files_written: int
    claude_md_updated: int
    errors: list[str]


def run_pipeline(settings: Settings) -> PipelineResult:
    """Run the full analysis pipeline.

    Steps:
    1. Ingest new conversations from Claude projects directory
    2. Classify projects by type
    3. Detect corrections in conversations
    4. Extract preferences from corrections
    5. Process review decisions from Obsidian
    6. Generate new Obsidian review notes
    7. Update CLAUDE.md files with approved rules
    """
    started_at = datetime.utcnow().isoformat()
    errors: list[str] = []

    # Initialize database
    db = get_database(settings.database.path)

    # Results tracking
    conversations_new = 0
    conversations_updated = 0
    projects_classified = 0
    corrections_detected = 0
    preferences_extracted = 0
    reviews_processed = 0
    rules_approved = 0
    obsidian_files = 0
    claude_md_updated = 0

    # Step 1: Ingest conversations
    logger.info("Step 1: Ingesting conversations...")
    try:
        # Use default device ID for now
        device_id = settings.devices[0].id if settings.devices else "default"

        new, updated = ingest_all_conversations(
            db,
            settings.sync.claude_projects_path,
            device_id,
        )
        conversations_new = new
        conversations_updated = updated
        logger.info(f"  Ingested {new} new, {updated} updated conversations")
    except Exception as e:
        errors.append(f"Ingest error: {e}")
        logger.error(f"  Error: {e}")

    # Step 2: Classify projects
    logger.info("Step 2: Classifying projects...")
    try:
        projects_classified = classify_projects_from_conversations(db)
        logger.info(f"  Classified {projects_classified} projects")
    except Exception as e:
        errors.append(f"Classification error: {e}")
        logger.error(f"  Error: {e}")

    # Step 3: Detect corrections
    logger.info("Step 3: Detecting corrections...")
    try:
        for correction in detect_all_corrections(db):
            save_correction(db, correction)
            corrections_detected += 1

            # Add high-confidence corrections to review queue
            if correction.confidence >= settings.analysis.review_threshold:
                if correction.extracted_rule:
                    add_to_review_queue(
                        db,
                        proposed_rule=correction.extracted_rule,
                        rule_type=correction.correction_type,
                        confidence=correction.confidence,
                        file_types=[correction.file_touched] if correction.file_touched else None,
                        evidence=[
                            {
                                "conversation_id": correction.conversation_id,
                                "project_path": correction.project_path,
                                "timestamp": correction.timestamp,
                                "message": correction.user_message,
                                "type": correction.correction_type,
                            }
                        ],
                    )

        logger.info(f"  Detected {corrections_detected} corrections")
    except Exception as e:
        errors.append(f"Correction detection error: {e}")
        logger.error(f"  Error: {e}")

    # Step 4: Extract preferences
    logger.info("Step 4: Extracting preferences...")
    try:
        preferences_extracted = process_corrections_to_preferences(db)
        logger.info(f"  Extracted {preferences_extracted} preferences")
    except Exception as e:
        errors.append(f"Preference extraction error: {e}")
        logger.error(f"  Error: {e}")

    # Step 5: Process review decisions
    logger.info("Step 5: Processing review decisions...")
    try:
        review_counts = process_review_files(db, settings.obsidian)
        reviews_processed = review_counts["processed"]
        rules_approved = review_counts["approved"]
        logger.info(
            f"  Processed {reviews_processed} decisions, "
            f"{rules_approved} approved"
        )
    except Exception as e:
        errors.append(f"Review processing error: {e}")
        logger.error(f"  Error: {e}")

    # Step 6: Generate Obsidian notes
    logger.info("Step 6: Generating Obsidian notes...")
    try:
        obsidian_counts = write_obsidian_notes(db, settings.obsidian)
        obsidian_files = sum(obsidian_counts.values())
        logger.info(f"  Wrote {obsidian_files} Obsidian files")
    except Exception as e:
        errors.append(f"Obsidian generation error: {e}")
        logger.error(f"  Error: {e}")

    # Step 7: Update CLAUDE.md files
    logger.info("Step 7: Updating CLAUDE.md files...")
    try:
        global_claude_dir = Path.home() / ".claude"
        claude_counts = update_all_claude_md_files(db, global_claude_dir)
        claude_md_updated = claude_counts["global"] + claude_counts["projects"]
        logger.info(
            f"  Updated {claude_counts['global']} global, "
            f"{claude_counts['projects']} project CLAUDE.md files"
        )
    except Exception as e:
        errors.append(f"CLAUDE.md update error: {e}")
        logger.error(f"  Error: {e}")

    completed_at = datetime.utcnow().isoformat()

    return PipelineResult(
        started_at=started_at,
        completed_at=completed_at,
        conversations_new=conversations_new,
        conversations_updated=conversations_updated,
        projects_classified=projects_classified,
        corrections_detected=corrections_detected,
        preferences_extracted=preferences_extracted,
        reviews_processed=reviews_processed,
        rules_approved=rules_approved,
        obsidian_files_written=obsidian_files,
        claude_md_updated=claude_md_updated,
        errors=errors,
    )


def run_pipeline_from_config(config_path: Path | None = None) -> PipelineResult:
    """Run the pipeline using configuration file."""
    from src.config import get_settings

    settings = get_settings(config_path)
    return run_pipeline(settings)
