FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml ./
COPY data_explorer ./data_explorer
COPY entrypoint.sh ./entrypoint.sh
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 explorer && mkdir -p /var/log/calmos-data-explorer && chown -R explorer:explorer /var/log/calmos-data-explorer
RUN chmod 755 /app/entrypoint.sh
EXPOSE 8090
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "data_explorer.main:app", "--host", "0.0.0.0", "--port", "8090"]
