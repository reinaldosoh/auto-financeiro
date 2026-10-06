FROM --platform=linux/amd64 python:3.11-slim-bookworm

ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA
ENV BANNER_HTTP_FORM_COMPLETO=1

ENV DEBIAN_FRONTEND=noninteractive
ENV DOCKER=true
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV HOME=/home/machine
ENV XDG_CACHE_HOME=/home/machine/.cache
ENV TOTP_STORE_PATH=/data/totp/chaves_totp.json
ENV CHROME_USER_DATA_DIR=/home/machine/.chrome-profiles

# Chrome, Xvfb e dependências para Selenium com display virtual
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg2 curl fonts-liberation libnss3 libxss1 gosu \
    libappindicator3-1 libasound2 libatk-bridge2.0-0 libatspi2.0-0 \
    libgtk-3-0 libgbm1 libdrm2 libx11-xcb1 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 xdg-utils ca-certificates \
    xvfb x11-utils dbus-x11 \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -r machine && useradd -r -g machine -d /home/machine -s /usr/sbin/nologin machine \
    && mkdir -p /app /data/totp /home/machine/.cache /home/machine/.chrome-profiles /tmp/.X11-unix \
    && chown -R machine:machine /app /data/totp /home/machine /tmp/.X11-unix

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R machine:machine /app

EXPOSE 8000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Entrypoint rebaixa para `machine`; Easypanel pode precisar de root inicial para chown do volume.
USER root

CMD ["/entrypoint.sh"]
