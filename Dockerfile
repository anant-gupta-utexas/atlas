FROM python:3.13-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files
COPY pyproject.toml ./

# Install dependencies
RUN uv venv && \
    . .venv/bin/activate && \
    uv sync

# Copy application code
COPY . .

# Activate virtual environment and set as default
ENV PATH="/app/.venv/bin:$PATH"

# Default command
CMD ["python", "main.py"]
