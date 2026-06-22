FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/uploads /app/outputs /app/templates && chmod +x /app/entrypoint.sh
EXPOSE 8000
CMD ["/app/entrypoint.sh"]
