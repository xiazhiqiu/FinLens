# FinScope Enterprise Dockerfile
# 多阶段构建

# Stage 1: 构建阶段
FROM python:3.11-slim as builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: 运行阶段
FROM python:3.11-slim

WORKDIR /app

# 复制 Python 依赖
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 创建非 root 用户
RUN groupadd -r finscope && useradd -r -g finscope finscope

# 复制应用代码
COPY src/ ./src/
COPY frontend/ ./frontend/
COPY config/ ./config/
COPY .env.example .

# 创建数据目录
RUN mkdir -p data/sqlite data/uploaded data/audit_logs && \
    chown -R finscope:finscope /app

# 切换到非 root 用户
USER finscope

# 暴露端口
EXPOSE 8501

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8501')" || exit 1

# 启动命令
CMD ["streamlit", "run", "frontend/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
