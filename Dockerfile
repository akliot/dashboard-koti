FROM python:3.12-slim

WORKDIR /app

COPY requirements_bot.txt .
RUN pip install --no-cache-dir -r requirements_bot.txt

COPY bot_telegram.py .

CMD ["python", "bot_telegram.py", "--webhook"]
