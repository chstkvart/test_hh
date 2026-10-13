FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8000
COPY app.py .
EXPOSE 8000
CMD ["python", "app.py"]
