# ===================================================
# TikTok Monitor — Dockerfile
# 支持 NVIDIA GPU（WSL2 + 4070Ti）
# ===================================================

FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖文件（利用 Docker 层缓存）
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 创建必要目录
RUN mkdir -p /app/data /app/reports

# 暴露端口
EXPOSE 5001

# 启动命令
CMD ["python", "app.py"]
