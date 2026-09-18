#!/usr/bin/env python3
"""Clear all responsibility sample data from oncall36hrs SQLite database.

This script cleans:
1. Candidate sample messages (unreviewed background messages not tied to alerts)
2. Sample reviews (sample_reviews)
3. Dataset version snapshots (dataset_versions)
4. Group split configurations (sample_group_splits)
5. AI evaluation run histories (ai_runs)
6. Message review labels and urgency tags (messages.label, messages.urgent)
7. Learning/sample settings (active_dataset, responsibility_rules, etc.)

Preserves:
- All alert messages, alert states, and delivery logs in the Inbox
- Connected group settings and tokens

Usage:
  python3 scripts/clear_responsibility_samples.py               # Execute full cleanup
  python3 scripts/clear_responsibility_samples.py --dry-run     # Preview what will be cleared
  python3 scripts/clear_responsibility_samples.py --keep-rules  # Keep the responsibility rules draft
"""
import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path


def run(db_path: str, dry_run: bool = False, keep_rules: bool = False) -> bool:
    db_file = Path(db_path)
    if not db_file.exists():
        print(f"Error: Database file not found at: {db_path}")
        return False

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    # Gather statistics
    stats = {}
    if "sample_reviews" in tables:
        stats["sample_reviews"] = cur.execute("SELECT count(*) FROM sample_reviews").fetchone()[0]
    if "dataset_versions" in tables:
        stats["dataset_versions"] = cur.execute("SELECT count(*) FROM dataset_versions").fetchone()[0]
    if "sample_group_splits" in tables:
        stats["sample_group_splits"] = cur.execute("SELECT count(*) FROM sample_group_splits").fetchone()[0]
    if "ai_runs" in tables:
        stats["ai_runs"] = cur.execute("SELECT count(*) FROM ai_runs").fetchone()[0]

    stats["messages_labeled"] = cur.execute(
        "SELECT count(*) FROM messages WHERE label IS NOT NULL OR urgent != 0"
    ).fetchone()[0]

    stats["candidate_messages"] = cur.execute("""
        SELECT count(*) FROM messages 
        WHERE (id NOT IN (SELECT message_id FROM alerts WHERE message_id IS NOT NULL))
          AND COALESCE(reason, '') != 'mention'
    """).fetchone()[0]

    settings_keys = ["active_dataset", "mention_migration_map", "mention_migration_retired"]
    if not keep_rules:
        settings_keys.append("responsibility_rules")

    existing_settings = [
        k for k in settings_keys
        if cur.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone()
    ]
    stats["settings_to_remove"] = existing_settings

    print("=== Responsibility Sample Cleanup Summary ===")
    print(f"Database: {db_path}")
    print(f"Mode: {'[DRY RUN - No changes applied]' if dry_run else '[EXECUTE]'}")
    print(f"  - Candidate messages to remove: {stats['candidate_messages']}")
    print(f"  - Sample reviews to remove: {stats.get('sample_reviews', 0)}")
    print(f"  - Message labels/urgency to reset: {stats['messages_labeled']}")
    print(f"  - Dataset versions to remove: {stats.get('dataset_versions', 0)}")
    print(f"  - Group splits to remove: {stats.get('sample_group_splits', 0)}")
    print(f"  - AI evaluation runs to remove: {stats.get('ai_runs', 0)}")
    print(f"  - Settings to clear: {', '.join(existing_settings) if existing_settings else 'None'}")

    if dry_run:
        print("\nDry run completed. Run without --dry-run to apply changes.")
        conn.close()
        return True

    # Automatic safety backup before changes
    backup_dir = db_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_file = backup_dir / f"before-clear-samples-{ts}.sqlite3"
    print(f"\nCreating safety backup at: {backup_file} ...")
    shutil.copy2(db_file, backup_file)

    with conn:
        if "sample_reviews" in tables:
            cur.execute("DELETE FROM sample_reviews")
        if "dataset_versions" in tables:
            cur.execute("DELETE FROM dataset_versions")
        if "sample_group_splits" in tables:
            cur.execute("DELETE FROM sample_group_splits")
        if "ai_runs" in tables:
            cur.execute("DELETE FROM ai_runs")

        cur.execute("UPDATE messages SET label = NULL, urgent = 0 WHERE label IS NOT NULL OR urgent != 0")

        cur.execute("""
            DELETE FROM messages 
            WHERE (id NOT IN (SELECT message_id FROM alerts WHERE message_id IS NOT NULL))
              AND COALESCE(reason, '') != 'mention'
        """)

        for key in settings_keys:
            cur.execute("DELETE FROM settings WHERE key=?", (key,))

    conn.close()
    print("Cleanup finished successfully.")
    print(f"Backup preserved at: {backup_file}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clear responsibility sample data in oncall36hrs")
    parser.add_argument(
        "--db-path",
        default="/home/ubuntu/oncall36hrs/data/app/alerts.sqlite3",
        help="Path to alerts.sqlite3 database",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying the database",
    )
    parser.add_argument(
        "--keep-rules",
        action="store_true",
        help="Do not clear the responsibility rules draft text",
    )
    args = parser.parse_args()
    run(db_path=args.db_path, dry_run=args.dry_run, keep_rules=args.keep_rules)
