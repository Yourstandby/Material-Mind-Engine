# MME 应用镜像（Web / 网关 / OCR 共用一个镜像，靠 command 区分）
#
# 构建（在仓库根目录）：
#   docker build -f deploy/docker/Dockerfile -t mme-app:local .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# opencv / Pillow 运行时依赖 + 健康检查用 curl
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-web.txt requirements-local.txt ./
RUN pip install -r requirements.txt -r requirements-web.txt -r requirements-local.txt

COPY . .

EXPOSE 8300 8100 8101
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8300", "--workers", "1"]
