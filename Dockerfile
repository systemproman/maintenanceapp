FROM python:3.11-slim

# Evita prompts durante install
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Instala dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY . .

# Cria pastas necessárias
RUN mkdir -p uploads assets

# Expõe a porta que o Render vai usar
EXPOSE 8080

# Roda a aplicação
CMD ["python", "main.py"]
