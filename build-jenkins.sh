#!/bin/bash
# ===================================================
# Jenkins CI 构建脚本
# 构建 Docker 镜像并推送到私有镜像仓库
#
# 仓库地址：192.168.2.111:80/car/leezcarco
# 用法：在 Jenkins Pipeline 中调用此脚本
# ===================================================

set -e  # 任意命令失败立即退出

# ==================== 配置 ====================
REGISTRY="192.168.2.111:80"
REPO="car/leezcarco"
IMAGE_NAME="${REGISTRY}/${REPO}"

# 版本号：优先用 Jenkins BUILD_NUMBER，否则用 git commit hash
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

# 登录私有镜像仓库
# 凭据建议用 Jenkins Credentials 注入，这里支持环境变量传入
if [ -n "${REGISTRY_USER}" ] && [ -n "${REGISTRY_PASS}" ]; then
    log "登录镜像仓库 ${REGISTRY} ..."
    echo "${REGISTRY_PASS}" | docker login "${REGISTRY}" -u "${REGISTRY_USER}" --password-stdin
else
    log "未检测到 REGISTRY_USER/REGISTRY_PASS，跳过登录（假设已登录）"
fi

# 构建镜像
log "构建镜像 ${FULL_IMAGE} ..."
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
log "构建推送完成！"
log "镜像：${FULL_IMAGE}"
log "最新：${LATEST_IMAGE}"
log "========================================"

# 清理本地悬空镜像（节省磁盘）
log "清理悬空镜像 ..."
docker image prune -f

log "全部完成 ✓"
