FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv pip install --system --no-cache --upgrade pip \
    && uv pip install --system --all-extras --project .

COPY . .

CMD ["python", "main.py"]
