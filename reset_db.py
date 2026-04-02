import os
from dotenv import load_dotenv
import psycopg

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não definida")

TABELAS = [
    "anexos",
    "os_anexos",
    "apontamentos",
    "ordens_servico",
    "ativos",
    "componentes",
    "equipamentos",
    "locais",
    "usuarios",
    "equipes",
    "funcionarios",
]

def tabela_existe(cur, nome_tabela: str) -> bool:
    cur.execute("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
        )
    """, (nome_tabela,))
    row = cur.fetchone()
    return bool(row[0]) if row else False

def main():
    print("🔥 Zerando banco...")

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            # desabilita checagem de FK temporariamente
            cur.execute("SET session_replication_role = replica;")

            try:
                for tabela in TABELAS:
                    try:
                        if not tabela_existe(cur, tabela):
                            print(f"⏭️ {tabela} não existe, pulando")
                            continue

                        cur.execute(f'TRUNCATE TABLE "{tabela}" RESTART IDENTITY CASCADE;')
                        print(f"✅ {tabela} limpa")

                    except Exception as e:
                        print(f"⚠️ erro em {tabela}: {e}")

            finally:
                cur.execute("SET session_replication_role = DEFAULT;")

    print("🚀 Banco zerado com sucesso")

if __name__ == "__main__":
    main()