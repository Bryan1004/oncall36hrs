#!/usr/bin/env python3
"""Migrate mention messages out of the responsibility sample system.

This script:
1. Removes sample_reviews for mention messages
2. Clears label/urgent on mention messages
3. Creates cleaned replacement versions for published snapshots containing mention samples
4. Updates active_dataset pointer if needed

Preserves: original messages, alerts, deliveries, connectors, groups, settings (except migration markers).

Usage:
    python scripts/migrate_mention_exclusion.py --dry-run     # Preview (default)
    python scripts/migrate_mention_exclusion.py --execute      # Execute migration
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path


MIGRATION_VERSION = 1


def has_review_text(text):
    if not text or not text.strip():
        return False
    return text.strip() != '[非文字消息]'


def get_setting(db, key):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_setting(db, key, value):
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))


def run(db_path, execute=False):
    if not Path(db_path).exists():
        print(f'ERROR: Database not found: {db_path}')
        return False

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.create_function('has_review_text', 1,
                       lambda text: int(has_review_text(text)), deterministic=True)

    mode = 'EXECUTE' if execute else 'DRY-RUN'
    print(f'=== Mention Sample Exclusion Migration ({mode}) ===')
    print(f'Database: {db_path}')
    print()

    # Check idempotency
    existing_version = get_setting(db, 'mention_migration_version')
    if existing_version and existing_version >= MIGRATION_VERSION:
        print(f'Migration version {existing_version} already applied. Nothing to do.')
        db.close()
        return True

    # Check for in-progress AI runs
    running = db.execute(
        "SELECT id, status FROM ai_runs WHERE status IN ('queued','running','cancel_requested')"
    ).fetchall()
    if running:
        print('ERROR: Active AI runs detected:')
        for r in running:
            print(f'  Run #{r["id"]}: {r["status"]}')
        print('Please wait for them to complete before migrating.')
        db.close()
        return False

    # === Pre-migration statistics ===
    print('--- Pre-migration Statistics ---')

    # Invariants for verification
    total_messages = db.execute('SELECT count(*) FROM messages').fetchone()[0]
    total_alerts = db.execute('SELECT count(*) FROM alerts').fetchone()[0]
    pending_alerts = db.execute("SELECT count(*) FROM alerts WHERE status='pending'").fetchone()[0]
    total_deliveries = db.execute('SELECT count(*) FROM deliveries').fetchone()[0]
    groups_count = db.execute('SELECT count(*) FROM groups').fetchone()[0]
    settings_count = db.execute('SELECT count(*) FROM settings').fetchone()[0]

    print(f'Total messages: {total_messages}')
    print(f'Total alerts: {total_alerts} (pending: {pending_alerts})')
    print(f'Total deliveries: {total_deliveries}')
    print(f'Groups: {groups_count}')
    print()

    # Mention statistics
    for platform in ('whatsapp', 'telegram'):
        mention_total = db.execute(
            "SELECT count(*) FROM messages WHERE platform=? AND reason='mention'",
            (platform,)).fetchone()[0]
        mention_reviewed = db.execute(
            "SELECT count(*) FROM messages WHERE platform=? AND reason='mention' AND label IS NOT NULL",
            (platform,)).fetchone()[0]
        mention_with_review = db.execute(
            "SELECT count(*) FROM sample_reviews WHERE message_id IN "
            "(SELECT id FROM messages WHERE platform=? AND reason='mention')",
            (platform,)).fetchone()[0]
        print(f'{platform}: {mention_total} mention messages, '
              f'{mention_reviewed} reviewed, {mention_with_review} with sample_reviews')

    # Version statistics
    versions = [dict(r) for r in db.execute(
        'SELECT id, samples FROM dataset_versions ORDER BY id').fetchall()]
    affected_versions = []
    for v in versions:
        samples = json.loads(v['samples'])
        mention_samples = [s for s in samples if s.get('reason') == 'mention']
        if not mention_samples:
            # Try to identify by cross-referencing with messages table
            mention_ids = {r[0] for r in db.execute(
                "SELECT id FROM messages WHERE platform IN ('whatsapp','telegram') AND reason='mention'"
            ).fetchall()}
            mention_samples = [s for s in samples if s.get('id') in mention_ids]
        if mention_samples:
            affected_versions.append({
                'id': v['id'],
                'total_samples': len(samples),
                'mention_count': len(mention_samples),
                'remaining': len(samples) - len(mention_samples)
            })

    print(f'\nVersions: {len(versions)} total, {len(affected_versions)} contain mention samples')
    for av in affected_versions:
        print(f'  V{av["id"]}: {av["mention_count"]} mention samples out of {av["total_samples"]}, '
              f'{av["remaining"]} remaining after cleanup')

    active_dataset = get_setting(db, 'active_dataset')
    print(f'\nActive dataset version: {active_dataset}')
    print()

    if not execute:
        print('--- DRY-RUN complete. Use --execute to apply changes. ---')
        db.close()
        return True

    # === Backup ===
    backup_dir = Path(db_path).parent / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    backup_path = backup_dir / f'alerts_pre_mention_migration_{timestamp}.sqlite3'
    print(f'Creating backup: {backup_path}')
    backup_db = sqlite3.connect(str(backup_path))
    db.backup(backup_db)
    backup_db.close()
    print('Backup complete.')
    print()

    # === Execute migration ===
    print('--- Executing Migration ---')

    mention_ids = [r[0] for r in db.execute(
        "SELECT id FROM messages WHERE platform IN ('whatsapp','telegram') AND reason='mention'"
    ).fetchall()]

    with db:
        # 1. Delete sample_reviews for mention messages
        deleted_reviews = db.execute(
            "DELETE FROM sample_reviews WHERE message_id IN "
            "(SELECT id FROM messages WHERE platform IN ('whatsapp','telegram') AND reason='mention')"
        ).rowcount
        print(f'Deleted {deleted_reviews} sample_reviews for mention messages')

        # 2. Clear label/urgent on mention messages
        cleared_labels = db.execute(
            "UPDATE messages SET label=NULL, urgent=0 "
            "WHERE platform IN ('whatsapp','telegram') AND reason='mention'"
        ).rowcount
        print(f'Cleared label/urgent on {cleared_labels} mention messages')

        # 3. Process published versions
        migration_map = get_setting(db, 'mention_migration_map') or {}
        retired_versions = get_setting(db, 'mention_migration_retired') or []
        mention_id_set = set(mention_ids)

        for v in versions:
            vid = v['id']
            if str(vid) in migration_map:
                print(f'  V{vid}: already migrated, skipping')
                continue

            samples = json.loads(v['samples'])
            # Filter out mention samples (by reason field or by cross-reference)
            cleaned = []
            removed_count = 0
            for s in samples:
                is_mention = s.get('reason') == 'mention'
                if not is_mention and s.get('id') in mention_id_set:
                    # Cross-reference: verify identity with messages table
                    msg = db.execute(
                        "SELECT platform, chat_id, external_id, reason FROM messages WHERE id=?",
                        (s['id'],)).fetchone()
                    if msg and msg['reason'] == 'mention' and \
                       msg['platform'] == s.get('platform') and \
                       msg['chat_id'] == s.get('chat_id'):
                        is_mention = True

                if is_mention:
                    removed_count += 1
                else:
                    cleaned.append(s)

            if removed_count == 0:
                print(f'  V{vid}: no mention samples, skipping')
                continue

            if cleaned:
                # Create replacement version
                original = db.execute(
                    'SELECT * FROM dataset_versions WHERE id=?', (vid,)).fetchone()
                note = f'从 V{vid} 清理 {removed_count} 条 mention 样本（自动迁移）'
                cur = db.execute(
                    'INSERT INTO dataset_versions(created, rules, note, samples) VALUES (?,?,?,?)',
                    (time.time(), original['rules'], note,
                     json.dumps(cleaned, ensure_ascii=False)))
                new_vid = cur.lastrowid
                migration_map[str(vid)] = new_vid
                print(f'  V{vid}: removed {removed_count} mention samples, '
                      f'created replacement V{new_vid} with {len(cleaned)} samples')
            else:
                # All samples were mention — mark as retired
                if vid not in retired_versions:
                    retired_versions.append(vid)
                print(f'  V{vid}: all {removed_count} samples were mention, '
                      f'marked as retired (no replacement created)')

        # 4. Update active_dataset if needed
        if active_dataset is not None:
            if str(active_dataset) in migration_map:
                new_active = migration_map[str(active_dataset)]
                set_setting(db, 'active_dataset', new_active)
                print(f'\nActive dataset updated: V{active_dataset} → V{new_active}')
            elif active_dataset in retired_versions:
                set_setting(db, 'active_dataset', None)
                print(f'\nActive dataset cleared: V{active_dataset} was retired '
                      f'(all samples were mention)')

        # 5. Persist migration state
        set_setting(db, 'mention_migration_map', migration_map)
        set_setting(db, 'mention_migration_retired', retired_versions)
        set_setting(db, 'mention_migration_version', MIGRATION_VERSION)

    print()

    # === Post-migration verification ===
    print('--- Post-migration Verification ---')

    post_messages = db.execute('SELECT count(*) FROM messages').fetchone()[0]
    post_alerts = db.execute('SELECT count(*) FROM alerts').fetchone()[0]
    post_pending = db.execute("SELECT count(*) FROM alerts WHERE status='pending'").fetchone()[0]
    post_deliveries = db.execute('SELECT count(*) FROM deliveries').fetchone()[0]
    post_groups = db.execute('SELECT count(*) FROM groups').fetchone()[0]

    checks = [
        ('Messages preserved', post_messages == total_messages),
        ('Alerts preserved', post_alerts == total_alerts),
        ('Pending alerts preserved', post_pending == pending_alerts),
        ('Deliveries preserved', post_deliveries == total_deliveries),
        ('Groups preserved', post_groups == groups_count),
    ]

    all_ok = True
    for label, ok in checks:
        status = '✓' if ok else '✗ FAILED'
        print(f'  {status} {label}')
        if not ok:
            all_ok = False

    # Verify no mention messages have labels anymore
    remaining_labeled = db.execute(
        "SELECT count(*) FROM messages WHERE platform IN ('whatsapp','telegram') "
        "AND reason='mention' AND label IS NOT NULL").fetchone()[0]
    ok = remaining_labeled == 0
    print(f'  {"✓" if ok else "✗ FAILED"} No labeled mention messages remaining '
          f'({remaining_labeled})')
    if not ok:
        all_ok = False

    remaining_reviews = db.execute(
        "SELECT count(*) FROM sample_reviews WHERE message_id IN "
        "(SELECT id FROM messages WHERE platform IN ('whatsapp','telegram') "
        "AND reason='mention')").fetchone()[0]
    ok = remaining_reviews == 0
    print(f'  {"✓" if ok else "✗ FAILED"} No sample_reviews for mention messages '
          f'({remaining_reviews})')
    if not ok:
        all_ok = False

    print()
    if all_ok:
        print(f'=== Migration complete. Backup: {backup_path} ===')
    else:
        print('=== VERIFICATION FAILED — review output above ===')

    db.close()
    return all_ok


def main():
    parser = argparse.ArgumentParser(
        description='Migrate mention messages out of the responsibility sample system')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true',
                       help='Preview changes without modifying the database')
    group.add_argument('--execute', action='store_true',
                       help='Execute the migration')
    parser.add_argument('--db', default=None,
                        help='Database path (default: from compose.yaml DATA_DIR)')

    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        # Try to resolve from compose.yaml / environment
        data_dir = os.environ.get('DATA_DIR', './data/app')
        db_path = str(Path(data_dir) / 'alerts.sqlite3')

    success = run(db_path, execute=args.execute)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
