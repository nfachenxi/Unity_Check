$ErrorActionPreference = "Stop"

uv sync --python 3.13

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; uv run uvicorn unity_check.main:app --app-dir src --host 0.0.0.0 --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; uv run celery -A unity_check.celery_app:celery_app worker --loglevel=INFO"
