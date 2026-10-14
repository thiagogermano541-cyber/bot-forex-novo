FROM python:3.12-slim AS checks
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY tests ./tests
RUN pip install --no-cache-dir '.[test]'
RUN python -m pytest -q && touch /tmp/jarvis-tests-passed

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY --from=checks /tmp/jarvis-tests-passed /app/.tests-passed
RUN useradd --create-home jarvis
USER jarvis
EXPOSE 8000
CMD ["python", "-m", "app.run"]
