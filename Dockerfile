# Dockerfile for NTrader application
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install UV (Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:$PATH"

# Copy project files
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY tests/ ./tests/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Install dependencies using UV
RUN uv sync --frozen

# Create directories for data and logs
RUN mkdir -p /app/data /app/logs /app/data/catalog

# Set Python path
ENV PYTHONPATH=/app

# Newer glibc (>= 2.41, e.g. Debian trixie / Ubuntu 26.04 bases) refuses to load
# nautilus_trader's extensions without this ("cannot enable executable stack").
# Older glibc ignores a tunable it does not know, so this is harmless on bookworm.
# Read by the loader at process start, so it must be in the image environment.
ENV GLIBC_TUNABLES=glibc.rtld.execstack=2

# Expose ports (optional, for future FastAPI integration)
EXPOSE 8000

# Default command (can be overridden in docker-compose)
CMD ["uv", "run", "python", "-m", "src.cli.main", "--help"]
