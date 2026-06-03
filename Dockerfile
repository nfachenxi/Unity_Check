FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Ensure SSH doesn't refuse keys with "bad permissions" in containers.
# Docker bind mounts typically produce 0777; we override StrictModes so
# the key file check is skipped.  The host machine is responsible for
# protecting the actual .ssh directory.
RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh && \
    printf 'Host *\n    StrictHostKeyChecking accept-new\n    StrictModes no\n' \
    > /etc/ssh/ssh_config.d/99-unity-check.conf

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
