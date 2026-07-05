FROM python:3.13-slim

# Prevents Python from writing pyc files to disk
ENV PYTHONDONTWRITEBYTECODE=1
# Prevents Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# Create a non-root user for security
RUN adduser --disabled-password --gecos '' appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai_service/ ./ai_service/
COPY app/ ./app/
COPY ["schema_llm_context 1.dbml", "./"]

# Change ownership of the code directory to the non-root user
RUN chown -R appuser:appuser /code

# Switch to the non-root user
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
