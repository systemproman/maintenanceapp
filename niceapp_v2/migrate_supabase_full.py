import os
import sys
import mimetypes
from pathlib import Path
from typing import Optional

try:
    import requests
except Exception as ex:
    print('Erro: requests não instalado.', ex)
    sys.exit(1)

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception as ex:
    print('Erro: psycopg não instalado. Instale com: pip install psycopg[binary] requests python-dotenv')
    print(ex)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv('.env')
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', str(BASE_DIR / 'uploads')))
UPLOAD_ATIVOS_DIR = Path(os.getenv('UPLOAD_ATIVOS_DIR', str(UPLOAD_DIR / 'ativos')))
LOCAL_ASSETS_DIR = Path(os.getenv('LOCAL_ASSETS_DIR', str(BASE_DIR / 'assets')))

DATABASE_URL = str(os.getenv('DATABASE_URL', '') or '').strip()
SUPABASE_URL = str(os.getenv('SUPABASE_URL', '') or '').strip().rstrip('/')
SUPABASE_KEY = str(
    os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    or os.getenv('SUPABASE_SERVICE_KEY')
    or os.getenv('SUPABASE_KEY')
    or os.getenv('SUPABASE_ANON_KEY')
    or ''
).strip()
SUPABASE_BUCKET_ASSETS = str(os.getenv('SUPABASE_BUCKET_ASSETS', 'assets') or 'assets').strip()
SUPABASE_BUCKET_UPLOADS = str(os.getenv('SUPABASE_BUCKET_UPLOADS', 'uploads') or 'uploads').strip()
SUPABASE_STORAGE_FOLDER_ATIVOS = str(os.getenv('SUPABASE_STORAGE_FOLDER_ATIVOS', 'ativos') or 'ativos').strip('/ ')
SUPABASE_STORAGE_FOLDER_OS = str(os.getenv('SUPABASE_STORAGE_FOLDER_OS', 'os') or 'os').strip('/ ')
FORCE_REUPLOAD = str(os.getenv('SUPABASE_FORCE_REUPLOAD', 'false') or 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
DELETE_LOCAL_AFTER_UPLOAD = str(os.getenv('DELETE_LOCAL_AFTER_UPLOAD', 'false') or 'false').strip().lower() in {'1', 'true', 'yes', 'on'}
TIMEOUT = int(os.getenv('SUPABASE_TIMEOUT', '120'))


def fail(msg: str) -> None:
    print(f'❌ {msg}')
    sys.exit(1)


def info(msg: str) -> None:
    print(f'• {msg}')


def ok(msg: str) -> None:
    print(f'✅ {msg}')


def warn(msg: str) -> None:
    print(f'⚠️ {msg}')


def headers(extra: Optional[dict] = None) -> dict:
    h = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
    }
    if extra:
        h.update(extra)
    return h


def guess_content_type(filename: str, tipo: str = '') -> str:
    tipo = str(tipo or '').upper()
    filename = str(filename or '')
    if tipo == 'PDF' or filename.lower().endswith('.pdf'):
        return 'application/pdf'
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or 'application/octet-stream'


def public_url(bucket: str, storage_path: str) -> str:
    return f'{SUPABASE_URL}/storage/v1/object/public/{bucket}/{storage_path.lstrip("/")}'


def upload_file(bucket: str, storage_path: str, local_file: Path, content_type: str) -> None:
    url = f'{SUPABASE_URL}/storage/v1/object/{bucket}/{storage_path.lstrip("/")}'
    with local_file.open('rb') as f:
        res = requests.post(
            url,
            headers=headers({
                'x-upsert': 'true',
                'content-type': content_type or 'application/octet-stream',
            }),
            data=f,
            timeout=TIMEOUT,
        )
    if res.status_code not in (200, 201):
        raise RuntimeError(f'{res.status_code} - {res.text[:500]}')


def ensure_env() -> None:
    if not DATABASE_URL:
        fail('DATABASE_URL não informado no .env')
    if not SUPABASE_URL:
        fail('SUPABASE_URL não informado no .env')
    if not SUPABASE_KEY:
        fail('SUPABASE_SERVICE_KEY / SERVICE_ROLE_KEY não informado no .env')


def connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)


def ensure_columns(conn) -> None:
    stmts = [
        "ALTER TABLE anexos ADD COLUMN IF NOT EXISTS bucket TEXT NULL",
        "ALTER TABLE anexos ADD COLUMN IF NOT EXISTS storage_path TEXT NULL",
        "ALTER TABLE anexos ADD COLUMN IF NOT EXISTS url_publica TEXT NULL",
        "ALTER TABLE os_anexos ADD COLUMN IF NOT EXISTS bucket TEXT NULL",
        "ALTER TABLE os_anexos ADD COLUMN IF NOT EXISTS storage_path TEXT NULL",
        "ALTER TABLE os_anexos ADD COLUMN IF NOT EXISTS url_publica TEXT NULL",
    ]
    with conn.cursor() as cur:
        for stmt in stmts:
            cur.execute(stmt)
    conn.commit()


def migrate_assets() -> tuple[int, int]:
    assets_map = {
        'logo_fsl.png': 'logos/logo_fsl.png',
        'logo_app.png': 'logos/logo_app.png',
        'fundo_fsl.png': 'backgrounds/fundo_fsl.png',
        'fundo_home.png': 'backgrounds/fundo_home.png',
    }
    sent = 0
    missing = 0
    info(f'Migrando assets locais de: {LOCAL_ASSETS_DIR}')
    for local_name, storage_path in assets_map.items():
        local_file = LOCAL_ASSETS_DIR / local_name
        if not local_file.exists() or not local_file.is_file():
            warn(f'Asset não encontrado: {local_file}')
            missing += 1
            continue
        upload_file(SUPABASE_BUCKET_ASSETS, storage_path, local_file, guess_content_type(local_name))
        ok(f'Asset enviado: {local_name} -> {SUPABASE_BUCKET_ASSETS}/{storage_path}')
        sent += 1
    return sent, missing


def fetch_rows(conn, table: str):
    query = f"SELECT * FROM {table}"
    if not FORCE_REUPLOAD:
        query += " WHERE COALESCE(bucket, '') = '' OR COALESCE(storage_path, '') = ''"
    query += " ORDER BY created_at NULLS LAST, id"
    with conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()


def migrate_table(conn, table: str, owner_field: str, root_folder: str) -> tuple[int, int, int]:
    rows = fetch_rows(conn, table)
    sent = 0
    skipped = 0
    failed = 0
    info(f'Migrando tabela {table}: {len(rows)} registro(s) elegível(is)')

    for row in rows:
        row_id = str(row.get('id') or '').strip()
        owner_id = str(row.get(owner_field) or '').strip()
        nome_salvo = str(row.get('nome_salvo') or '').strip()
        nome_original = str(row.get('nome_original') or nome_salvo or row_id).strip()
        caminho = Path(str(row.get('caminho') or '').strip())

        if not row_id or not owner_id:
            warn(f'{table}:{row_id or "<sem-id>"} sem owner/id, ignorado')
            skipped += 1
            continue

        if not caminho.exists() or not caminho.is_file():
            warn(f'{table}:{row_id} arquivo local não encontrado: {caminho}')
            failed += 1
            continue

        if not nome_salvo:
            nome_salvo = f'{row_id}{caminho.suffix.lower()}'

        storage_path = f'{root_folder}/{owner_id}/{nome_salvo}'

        try:
            upload_file(
                SUPABASE_BUCKET_UPLOADS,
                storage_path,
                caminho,
                guess_content_type(nome_original, str(row.get('tipo') or '')),
            )
            with conn.cursor() as cur:
                cur.execute(
                    f'''UPDATE {table}
                        SET bucket = %s,
                            storage_path = %s,
                            url_publica = %s
                        WHERE id = %s''',
                    (SUPABASE_BUCKET_UPLOADS, storage_path, public_url(SUPABASE_BUCKET_UPLOADS, storage_path), row_id),
                )
            conn.commit()
            if DELETE_LOCAL_AFTER_UPLOAD:
                try:
                    caminho.unlink(missing_ok=True)
                except Exception:
                    pass
            ok(f'{table}:{row_id} -> {SUPABASE_BUCKET_UPLOADS}/{storage_path}')
            sent += 1
        except Exception as ex:
            conn.rollback()
            warn(f'Falha em {table}:{row_id}: {ex}')
            failed += 1

    return sent, skipped, failed


def main() -> int:
    ensure_env()

    print('=== MIGRAÇÃO FULL PARA SUPABASE STORAGE ===')
    print(f'BASE_DIR: {BASE_DIR}')
    print(f'UPLOAD_ATIVOS_DIR: {UPLOAD_ATIVOS_DIR}')
    print(f'LOCAL_ASSETS_DIR: {LOCAL_ASSETS_DIR}')
    print(f'Bucket assets: {SUPABASE_BUCKET_ASSETS}')
    print(f'Bucket uploads: {SUPABASE_BUCKET_UPLOADS}')
    print('')

    try:
        assets_sent, assets_missing = migrate_assets()
    except Exception as ex:
        warn(f'Falha na migração de assets: {ex}')
        assets_sent, assets_missing = 0, 0

    conn = connect()
    try:
        ensure_columns(conn)
        anexos_sent, anexos_skipped, anexos_failed = migrate_table(conn, 'anexos', 'ativo_id', SUPABASE_STORAGE_FOLDER_ATIVOS)
        os_sent, os_skipped, os_failed = migrate_table(conn, 'os_anexos', 'os_id', SUPABASE_STORAGE_FOLDER_OS)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print('\n=== RESUMO ===')
    print(f'Assets enviados: {assets_sent}')
    print(f'Assets faltando localmente: {assets_missing}')
    print(f'Anexos de ativos enviados: {anexos_sent}')
    print(f'Anexos de ativos ignorados: {anexos_skipped}')
    print(f'Anexos de ativos com falha: {anexos_failed}')
    print(f'Anexos de OS enviados: {os_sent}')
    print(f'Anexos de OS ignorados: {os_skipped}')
    print(f'Anexos de OS com falha: {os_failed}')

    total_failed = anexos_failed + os_failed
    if total_failed:
        warn('Migração concluída com pendências. Veja as linhas de aviso acima.')
        return 2
    ok('Migração concluída.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
