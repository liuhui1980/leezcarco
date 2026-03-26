#!/bin/bash
# ===================================================
# 服务器端部署脚本
# 从私有镜像仓库拉取最新镜像并启动容器
#
# 使用场景：
#   - Jenkins 构建完成后，SSH 到服务器执行此脚本
#   - 或手动在服务器上执行
#
# 用法：
#   ./deploy.sh                    # 使用 latest 标签
#   ./deploy.sh build-42           # 使用指定 tag
# ===================================================

set -e

# ==================== 配置 ====================
REGISTRY="192.168.2.111:80"
REPO="car/leezcarco"
IMAGE_NAME="${REGISTRY}/${REPO}"
TAG="${1:-latest}"                  # 默认 latest，可传参指定 tag
FULL_IMAGE="${IMAGE_NAME}:${TAG}"

CONTAINER_NAME="tiktok-monitor"
APP_PORT=5001
DATA_DIR="/opt/leezcarco/data"
REPORTS_DIR="/opt/leezcarco/reports"
CONFIG_FILE="/opt/leezcarco/config.py"

# ==================== 日志函数 ====================
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# ==================== 前置检查 ====================
if [ ! -f "${CONFIG_FILE}" ]; then
    log "❌ 错误：配置文件不存在：${CONFIG_FILE}"
    log "请先创建配置文件（参考 config.example.py）"
    exit 1
fi

# ==================== 创建目录 ====================
mkdir -p "${DATA_DIR}" "${REPORTS_DIR}"

# ==================== 拉取镜像 ====================
log "拉取镜像 ${FULL_IMAGE} ..."
docker pull "${FULL_IMAGE}"

# ==================== 停止旧容器 ====================
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "停止旧容器 ${CONTAINER_NAME} ..."
    docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    docker rm "${CONTAINER_NAME}" 2>/dev/null || true
fi

# ==================== 启动新容器 ====================
log "启动容器 ${CONTAINER_NAME} ..."

docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${APP_PORT}:${APP_PORT}" \
    -v "${DATA_DIR}:/app/data" \
    -v "${REPORTS_DIR}:/app/reports" \
    -v "${CONFIG_FILE}:/app/config.py:ro" \
    -v "whisper-cache:/root/.cache/whisper" \
    -e PYTHONUNBUFFERED=1 \
    --gpus all \
    "${FULL_IMAGE}"

# ==================== 等待健康检查 ====================
log "等待服务启动（最多60秒）..."
for i in $(seq 1 12); do
    sleep 5
    if docker exec "${CONTAINER_NAME}" curl -sf http://localhost:${APP_PORT}/ > /dev/null 2>&1; then
        log "✅ 服务已启动！"
        break
    fi
    if [ $i -eq 12 ]; then
        log "⚠️  健康检查超时，请手动确认容器状态"
        docker logs --tail 30 "${CONTAINER_NAME}"
    fi
    log "等待中... ($((i*5))s)"
done

# ==================== 清理旧镜像 ====================
log "清理悬空镜像..."
docker image prune -f

log "========================================"
log "部署完成！"
log "镜像：${FULL_IMAGE}"
log "容器：${CONTAINER_NAME}"
log "访问：http://localhost:${APP_PORT}"
log "      https://leezcarco.v.qxhua21.cn（需 Nginx）"
log "========================================"
