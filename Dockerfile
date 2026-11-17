FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[service]"
ENV GRADIAL_AUTOSTART=1
EXPOSE 8000
CMD ["uvicorn", "gradial_contracts.service.app:app", "--host", "0.0.0.0", "--port", "8000"]
