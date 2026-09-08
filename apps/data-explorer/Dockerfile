FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml ./
COPY data_explorer ./data_explorer
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 explorer && mkdir -p /var/log/calmos-data-explorer && chown -R explorer:explorer /var/log/calmos-data-explorer
USER explorer
EXPOSE 8090
CMD ["uvicorn", "data_explorer.main:app", "--host", "0.0.0.0", "--port", "8090"]
