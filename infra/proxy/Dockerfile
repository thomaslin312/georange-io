FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "aiohttp>=3.9,<4"
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 9000 9010
CMD ["python", "/app/app.py"]
