FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/data/huggingface \
    TORCH_HOME=/app/data/torch

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# CPU-only PyTorch keeps the Render image smaller and avoids CUDA dependencies.
RUN pip install --upgrade pip \
    && pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]
