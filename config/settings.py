import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega .env
load_dotenv(dotenv_path=".env")

# ==============================
# BANCO
# ==============================
DB_MODE = os.getenv('DB_MODE', 'sqlite').strip().lower()
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()

# ==============================
# PATHS
# ==============================
BASE_DIR = Path(__file__).resolve().parent.parent

SQLITE_PATH = Path(
    os.getenv('SQLITE_PATH', str(BASE_DIR / 'database.db'))
)

UPLOAD_DIR = Path(
    os.getenv('UPLOAD_DIR', str(BASE_DIR / 'uploads'))
)

UPLOAD_ATIVOS_DIR = Path(
    os.getenv('UPLOAD_ATIVOS_DIR', str(UPLOAD_DIR / 'ativos'))
)

# ==============================
# CONFIGS
# ==============================
ENABLE_CRITICIDADE = os.getenv(
    'ENABLE_CRITICIDADE', 'true'
).strip().lower() in {'1', 'true', 'yes', 'on'}

TIPOS_ATIVO = ['LOCAL', 'EQUIPAMENTO', 'COMPONENTE']
TIPOS_ANEXO = ['FOTO', 'PDF']

# ==============================
# GARANTE PASTAS
# ==============================
for pasta in [UPLOAD_DIR, UPLOAD_ATIVOS_DIR]:
    pasta.mkdir(parents=True, exist_ok=True)

# ==============================
# DEBUG (TEMPORÁRIO)
# ==============================
print("🔥 DB_MODE:", DB_MODE)
print("🔥 DATABASE_URL carregada?", bool(DATABASE_URL))