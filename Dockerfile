FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir -c requirements.lock -e .

# 캐시 디렉토리 (런타임에 lifespan 이 KB 데이터허브에서 최초 수집)
RUN mkdir -p data/cache

ENV HOST=0.0.0.0 PORT=8765
EXPOSE 8765
CMD ["sh", "-c", "signal serve --host 0.0.0.0 --port ${PORT:-8765}"]
