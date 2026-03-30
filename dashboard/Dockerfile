FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN echo '[safe]\n\tdirectory = *' > /etc/gitconfig
WORKDIR /app
CMD ["python3", "/app/server.py"]
