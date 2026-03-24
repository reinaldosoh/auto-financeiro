FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV DOCKER=true
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Chrome, Xvfb e dependências para Selenium com display virtual
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg2 curl fonts-liberation libnss3 libxss1 \
    libappindicator3-1 libasound2 libatk-bridge2.0-0 libatspi2.0-0 \
    libgtk-3-0 libgbm1 libdrm2 libx11-xcb1 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 xdg-utils ca-certificates \
    xvfb x11-utils dbus-x11 \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Script de entrada: inicia Xvfb e depois o servidor
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
