FROM python:3.12-slim
WORKDIR /app

# Build args — 内网部署时可指定镜像源
#   docker build --build-arg PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ ...
ARG PIP_INDEX=""
ARG PIP_EXTRA_INDEX=""

# Install system dependencies
# - Tesseract OCR with Chinese language support (文档 OCR 识别)
# - antiword (旧版 .doc 文件解析)
# - libgl1 (OpenCV/sentence-transformers 依赖)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    antiword \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN if [ -n "${PIP_INDEX}" ]; then \
        pip install --no-cache-dir -i "${PIP_INDEX}" $( [ -n "${PIP_EXTRA_INDEX}" ] && echo "--extra-index-url ${PIP_EXTRA_INDEX}" ) -r requirements.txt ; \
    else \
        pip install --no-cache-dir -r requirements.txt ; \
    fi

# Copy application code
COPY . .

# Create runtime directories and set permissions
RUN mkdir -p /app/uploads /app/outputs /app/templates /app/chroma_data && \
    chmod +x /app/entrypoint.sh

EXPOSE 8000

CMD ["/app/entrypoint.sh"]
