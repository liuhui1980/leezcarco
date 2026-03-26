# ===================================================
# TikTok Monitor — Dockerfile
# 支持 NVIDIA GPU（WSL2 + 4070Ti）
# 适配 Jenkins CI 构建
# ===================================================

FROM python:3.11-slim

# 构建参数（由 Jenkins build-jenkins.sh 传入）
ARG BUILD_DATE=""
ARG VERSION="latest"

# 镜像元数据
LABEL maintainer="leezcarco" \
      version="${VERSION}" \
      build.date="${BUILD_DATE}" \
      description="TikTok Live Monitor - 直播效果自动监测系统"

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖文件（利用 Docker 层缓存加速重复构建）
COPY requirements.txt .

# 安装 Python 依赖
# pip 使用国内镜像加速（CI 环境网络可能受限）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 复制项目代码
COPY . .

# 创建必要目录
RUN mkdir -p /app/data /app/reports

# 暴露端口
EXPOSE 5001

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5001/ || exit 1

# 启动命令
CMD ["python", "app.py"]
