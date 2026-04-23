import os
from dotenv import load_dotenv
import psycopg2

load_dotenv(dotenv_path=".env")  # 👈 AQUI

db_url = os.getenv("DATABASE_URL")

print("DATABASE_URL encontrada?", bool(db_url))
print("Valor lido:", db_url[:40] + "..." if db_url else "NENHUMA")

try:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT version();")
    print("✅ Conectado com sucesso!")
    print(cur.fetchone())
    conn.close()
except Exception as e:
    print("❌ Erro na conexão:")
    print(e)