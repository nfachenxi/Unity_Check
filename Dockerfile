FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for dependency management
RUN pip install --no-cache-dir uv

# Copy dependency declarations first (better layer caching)
COPY pyproject.toml ./
RUN uv sync --frozen

# Copy application source
COPY src/ ./src/

# repos dir for bare clone storage; mount as volume in production
RUN mkdir -p /app/repos

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "unity_check.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
