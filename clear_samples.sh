#!/usr/bin/env bash
set -e

# oncall36hrs 职责样本一键清理脚本
# 作用：清理所有待审核候选消息、审核记录、数据集版本、AI 试判数据，保留收件箱中的真实 @ 告警

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="${PROJECT_DIR}/data/app/alerts.sqlite3"
BACKUP_DIR="${PROJECT_DIR}/data/app/backups"

if [ ! -f "$DB_PATH" ]; then
  echo "❌ 错误：未找到数据库文件 $DB_PATH"
  exit 1
fi

mkdir -p "$BACKUP_DIR"
TS=$(date +"%Y%m%d-%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/before-clear-samples-${TS}.sqlite3"

echo "=========================================="
echo "      oncall36hrs 职责样本数据清理"
echo "=========================================="
echo "📁 数据库路径: $DB_PATH"
echo "💾 自动创建备份: $BACKUP_FILE"
cp "$DB_PATH" "$BACKUP_FILE"

python3 - <<EOF
import sqlite3

db = sqlite3.connect("$DB_PATH")
cur = db.cursor()

tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

with db:
    # 1. 清理审核记录
    deleted_reviews = 0
    if "sample_reviews" in tables:
        deleted_reviews = cur.execute("SELECT count(*) FROM sample_reviews").fetchone()[0]
        cur.execute("DELETE FROM sample_reviews")

    # 2. 清理数据集快照版本
    deleted_versions = 0
    if "dataset_versions" in tables:
        deleted_versions = cur.execute("SELECT count(*) FROM dataset_versions").fetchone()[0]
        cur.execute("DELETE FROM dataset_versions")

    # 3. 清理群组切分分配
    deleted_splits = 0
    if "sample_group_splits" in tables:
        deleted_splits = cur.execute("SELECT count(*) FROM sample_group_splits").fetchone()[0]
        cur.execute("DELETE FROM sample_group_splits")

    # 4. 清理 AI 试判运行记录
    deleted_runs = 0
    if "ai_runs" in tables:
        deleted_runs = cur.execute("SELECT count(*) FROM ai_runs").fetchone()[0]
        cur.execute("DELETE FROM ai_runs")

    # 5. 重置消息标签
    cur.execute("UPDATE messages SET label = NULL, urgent = 0 WHERE label IS NOT NULL OR urgent != 0")

    # 6. 清理非 @ 告警的待审核候选消息（彻底清空职责样本页面列表）
    deleted_candidates = cur.execute("""
        SELECT count(*) FROM messages 
        WHERE (id NOT IN (SELECT message_id FROM alerts WHERE message_id IS NOT NULL))
          AND COALESCE(reason, '') != 'mention'
    """).fetchone()[0]
    cur.execute("""
        DELETE FROM messages 
        WHERE (id NOT IN (SELECT message_id FROM alerts WHERE message_id IS NOT NULL))
          AND COALESCE(reason, '') != 'mention'
    """)

    # 7. 清理职责相关配置
    settings_to_delete = ["active_dataset", "mention_migration_map", "mention_migration_retired", "responsibility_rules"]
    for key in settings_to_delete:
        cur.execute("DELETE FROM settings WHERE key=?", (key,))

db.close()

print(f"✅ 清理完成：")
print(f"  - 候选待审核消息删除: {deleted_candidates} 条")
print(f"  - 人工审核记录删除:   {deleted_reviews} 条")
print(f"  - 快照版本删除:       {deleted_versions} 个")
print(f"  - 群组划分删除:       {deleted_splits} 条")
print(f"  - AI 试判记录删除:    {deleted_runs} 条")
print(f"  - 职责草稿与版本配置: 已重置")
EOF

echo "------------------------------------------"
echo "🎉 职责样本数据已全部清除完毕！"
echo "💡 提示：收件箱中的真实 @ 提醒与告警数据已完整保留。"
echo "=========================================="
