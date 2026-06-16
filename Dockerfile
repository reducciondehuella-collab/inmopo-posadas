FROM python:3.11-slim

WORKDIR /app

# Copiar dependencias primero (caché de Docker)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el proyecto
COPY . .

# Crear carpeta de datos persistente
RUN mkdir -p data

# Exponer puerto (Render usa $PORT)
EXPOSE 8000

# Comando de inicio
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
