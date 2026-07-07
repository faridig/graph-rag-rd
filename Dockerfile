FROM python:3.11-slim
WORKDIR /app

RUN useradd --uid 10001 --no-create-home --shell /bin/false app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

USER app
EXPOSE 8001
ENV PYTHONPATH=/app

CMD ["chainlit", "run", "src/chainlit_app.py", "--port", "8001", "--host", "0.0.0.0"]
