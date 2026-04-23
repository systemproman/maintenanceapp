import sqlite3
from pathlib import Path

from config.settings import SQLITE_PATH, UPLOAD_DIR, UPLOAD_ATIVOS_DIR

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_ATIVOS_DIR.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(SQLITE_PATH)
cur = conn.cursor()

cur.execute('PRAGMA foreign_keys = ON')

cur.execute("""
CREATE TABLE IF NOT EXISTS ativos (
    id TEXT PRIMARY KEY,
    tag TEXT NOT NULL UNIQUE,
    tag_base TEXT,
    descricao TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK(tipo IN ('LOCAL', 'EQUIPAMENTO', 'COMPONENTE')),
    parent_id TEXT NULL,
    criticidade INTEGER NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(parent_id) REFERENCES ativos(id) ON DELETE RESTRICT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS anexos (
    id TEXT PRIMARY KEY,
    ativo_id TEXT NOT NULL,
    nome_original TEXT NOT NULL,
    nome_salvo TEXT NOT NULL,
    caminho TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK(tipo IN ('FOTO', 'PDF')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(ativo_id) REFERENCES ativos(id) ON DELETE CASCADE
)
""")

cur.execute("""
CREATE INDEX IF NOT EXISTS idx_ativos_parent_id
ON ativos(parent_id)
""")

cur.execute("""
CREATE INDEX IF NOT EXISTS idx_ativos_tipo
ON ativos(tipo)
""")

cur.execute("""
CREATE INDEX IF NOT EXISTS idx_anexos_ativo_id
ON anexos(ativo_id)
""")

conn.commit()
conn.close()

print('Banco inicializado com sucesso.')