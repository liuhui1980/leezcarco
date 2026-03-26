#!/bin/bash
# ===================================================
# Jenkins CI 构建脚本
# 构建 Docker 镜像并推送到私有镜像仓库
#
# 仓库：192.168.2.111:80/car/leezcarco
# 直接在 Jenkins 执行此脚本即可，无需额外配置
# ===================================================

set -e

# ==================== 写死配置 ====================
REGISTRY="192.168.2.111:80"
REPO="car/leezcarco"
IMAGE_NAME="${REGISTRY}/${REPO}"

# 镜像仓库登录凭据（按实际填写）
REGISTRY_USER="admin"
REGISTRY_PASS="Harbor12345"

# 版本 tag：优先用 Jenkins BUILD_NUMBER，否则用 git commit hash
if [ -n "${BUILD_NUMBER}" ]; then
    TAG="build-${BUILD_NUMBER}"
else
    TAG=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
fi

FULL_IMAGE="${IMAGE_NAME}:${TAG}"
LATEST_IMAGE="${IMAGE_NAME}:latest"

# ==================== 日志函数 ====================
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# ==================== 开始构建 ====================
log "========================================"
log "开始构建 TikTok Monitor"
log "镜像：${FULL_IMAGE}"
log "========================================"

# 登录镜像仓库
log "登录镜像仓库 ${REGISTRY} ..."
echo "${REGISTRY_PASS}" | docker login "${REGISTRY}" -u "${REGISTRY_USER}" --password-stdin

# 构建镜像
log "构建镜像 ..."
docker build \
    --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --build-arg VERSION="${TAG}" \
    --label "build.number=${BUILD_NUMBER:-local}" \
    --label "build.time=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --label "git.commit=$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')" \
    -t "${FULL_IMAGE}" \
    -t "${LATEST_IMAGE}" \
    .

log "镜像构建完成"

# 推送镜像
log "推送 ${FULL_IMAGE} ..."
docker push "${FULL_IMAGE}"

log "推送 ${LATEST_IMAGE} ..."
docker push "${LATEST_IMAGE}"

log "========================================"
log "全部完成 ✓"
log "镜像：${FULL_IMAGE}"
log "最新：${LATEST_IMAGE}"
log "========================================"
