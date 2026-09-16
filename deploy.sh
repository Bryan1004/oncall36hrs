#!/usr/bin/env bash
# ==============================================================================
# 自动化更新与容器重建脚本 (Auto Update & Docker Rebuild)
# 用途：检测 Git 远程分支更新，自动 git pull 并安全重建/重启 Docker 容器
# ==============================================================================

set -euo pipefail

# 确保切换到项目根目录执行
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# 颜色与输出格式
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')] [INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')] [SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] [WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR]${NC} $1"
}

# 1. 检查 Docker 及 Docker Compose 环境
if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    log_error "未检测到 docker compose 或 docker-compose，请先安装 Docker！"
    exit 1
fi

# 2. 检查 Git 仓库状态
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log_error "当前目录不是有效的 Git 仓库！"
    exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
log_info "当前分支: ${CURRENT_BRANCH}"

# 检查是否传入了 --force / -f 参数（强制重建）
FORCE_REBUILD=false
if [[ "${1:-}" == "--force" || "${1:-}" == "-f" ]]; then
    FORCE_REBUILD=true
    log_warn "检测到强制构建参数 (-f / --force)，将跳过更新检查直接重建容器。"
fi

if [ "$FORCE_REBUILD" = false ]; then
    log_info "正在检查远程仓库更新..."
    git fetch origin "${CURRENT_BRANCH}" --quiet

    LOCAL_HASH="$(git rev-parse HEAD)"
    REMOTE_HASH="$(git rev-parse "@{u}" 2>/dev/null || git rev-parse "origin/${CURRENT_BRANCH}")"

    if [ "${LOCAL_HASH}" = "${REMOTE_HASH}" ]; then
        log_success "代码已是最新 (${LOCAL_HASH:0:7})，无需更新。"
        echo -e "💡 提示：如需强制重建容器，可执行: ${YELLOW}./deploy.sh --force${NC}"
        exit 0
    fi

    log_warn "检测到新版本代码！"
    echo "  本地版本: ${LOCAL_HASH:0:7}"
    echo "  远程版本: ${REMOTE_HASH:0:7}"

    # 3. 检查并暂存本地未提交的改动
    STASHED=false
    if ! git diff-index --quiet HEAD --; then
        log_warn "工作区有未提交的改动，正在自动保存暂存区 (git stash)..."
        git stash --quiet
        STASHED=true
    fi

    # 4. 拉取最新代码
    log_info "正在拉取最新代码 (git pull)..."
    git pull origin "${CURRENT_BRANCH}"

    if [ "$STASHED" = true ]; then
        log_info "恢复先前的本地暂存改动 (git stash pop)..."
        git stash pop --quiet || log_warn "恢复暂存改动时发生冲突，请手动检查 git status"
    fi
fi

# 5. 重建并启动 Docker 容器
log_info "开始重新构建并部署容器..."
${COMPOSE_CMD} build --pull
${COMPOSE_CMD} up -d --remove-orphans

# 6. 清理残留的悬空镜像 (dangling images)
log_info "清理旧版本悬空无用镜像..."
docker image prune -f >/dev/null 2>&1 || true

# 7. 打印最终容器运行状态
log_success "服务更新完成！当前运行容器状态："
${COMPOSE_CMD} ps
