FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# OpenSSH 10.0 removed StrictModes option entirely.
# StrictHostKeyChecking=accept-new is passed via -o in git_service.py.
# We still accept the host key fingerprint so Git can clone over SSH.
RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh && \
    printf 'Host *\n' > /etc/ssh/ssh_config.d/99-unity-check.conf

WORKDIR /app

# Install uv for dependency management
RUN pip install --no-cache-dir uv

# Copy dependency declarations first (better layer caching)
COPY pyproject.toml uv.lock ./
# Copy source so hatchling can discover the package for editable install
COPY src/ ./src/
RUN uv sync --frozen

# repos dir for bare clone storage; mount as volume in production
RUN mkdir -p /app/repos

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "unity_check.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
