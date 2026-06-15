FROM python:3.11-slim

# 时区设为国内（定时任务按本地时间）
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install -r requirements.txt

# 拷贝代码
COPY app ./app

# 非 root 运行
RUN useradd -r -u 1000 appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

EXPOSE 8000

# uvicorn 直接起；容器内通过 PORT 环境变量覆盖端口
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
