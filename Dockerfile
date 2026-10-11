FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    unzip \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 — yt-dlp требует >=20 для JS-челленджей
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /root/.config/yt-dlp/plugins \
    && curl -L -o /tmp/bgutil.zip \
       https://github.com/Brainicism/bgutil-ytdlp-pot-provider/releases/download/2.0.2/bgutil-ytdlp-pot-provider.zip \
    && unzip /tmp/bgutil.zip -d /root/.config/yt-dlp/plugins/ \
    && rm /tmp/bgutil.zip

COPY . .

CMD ["python", "-u", "bot.py"]