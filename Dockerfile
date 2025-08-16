# Используем Python
FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Указываем порт (Cloud Run сам подставит $PORT)
ENV PORT=8080

# Запуск приложения
CMD ["gunicorn", "-b", ":8080", "main:app"]
