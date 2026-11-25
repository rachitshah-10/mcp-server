FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir setuptools mcp openai pytest

COPY . .
# Install the package in editable mode so stdio child subprocesses can import it.
RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

CMD ["pytest"]
