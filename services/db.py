import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Any

try:
    import requests
except Exception:
    requests = None

from config.settings import DB_MODE, DATABASE_URL, SQLITE_PATH, UPLOAD_ATIVOS_DIR

Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
UPLOAD_ATIVOS_DIR.mkdir(parents=True, exist_ok=True)

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
SUPABASE_ASSETS_ENABLED = str(os.getenv('SUPABASE_ASSETS_ENABLED', 'true') or 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
SUPABASE_UPLOADS_ENABLED = str(os.getenv('SUPABASE_UPLOADS_ENABLED', 'true') or 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
SUPABASE_STORAGE_FOLDER_ATIVOS = str(os.getenv('SUPABASE_STORAGE_FOLDER_ATIVOS', 'ativos') or 'ativos').strip('/ ')
SUPABASE_STORAGE_FOLDER_OS = str(os.getenv('SUPABASE_STORAGE_FOLDER_OS', 'os') or 'os').strip('/ ')
SUPABASE_CACHE_DIR = Path(os.getenv('SUPABASE_CACHE_DIR', str(UPLOAD_ATIVOS_DIR.parent / '_supabase_cache')))
SUPABASE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_ASSETS_DIR = Path(os.getenv('LOCAL_ASSETS_DIR', 'assets'))
LOCAL_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

def _supabase_headers(extra: Optional[dict] = None) -> dict:
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
    }
    if extra:
        headers.update(extra)
    return headers

def _supabase_ready() -> bool:
    return bool(requests and SUPABASE_URL and SUPABASE_KEY)

def _supabase_upload_bytes(bucket: str, storage_path: str, payload: bytes, content_type: str = 'application/octet-stream') -> Optional[str]:
    if not _supabase_ready():
        return None
    storage_path = str(storage_path or '').lstrip('/')
    url = f'{SUPABASE_URL}/storage/v1/object/{bucket}/{storage_path}'
    res = requests.post(
        url,
        headers=_supabase_headers({
            'x-upsert': 'true',
            'content-type': content_type or 'application/octet-stream',
        }),
        data=payload,
        timeout=60,
    )
    if res.status_code not in (200, 201):
        raise RuntimeError(f'Falha ao subir arquivo no Supabase Storage: {res.text[:240]}')
    return storage_path

def _supabase_delete_object(bucket: str, storage_path: str) -> None:
    if not (_supabase_ready() and storage_path):
        return
    try:
        url = f'{SUPABASE_URL}/storage/v1/object/{bucket}/{str(storage_path).lstrip("/")}'
        requests.delete(url, headers=_supabase_headers(), timeout=30)
    except Exception:
        pass

def _supabase_download_object(bucket: str, storage_path: str) -> Optional[bytes]:
    if not (_supabase_ready() and storage_path):
        return None
    storage_path = str(storage_path).lstrip('/')
    candidates = [
        f'{SUPABASE_URL}/storage/v1/object/authenticated/{bucket}/{storage_path}',
        f'{SUPABASE_URL}/storage/v1/object/public/{bucket}/{storage_path}',
    ]
    last_error = None
    for url in candidates:
        try:
            res = requests.get(url, headers=_supabase_headers(), timeout=60)
            if res.status_code == 200:
                return res.content
            last_error = res.text[:240]
        except Exception as ex:
            last_error = str(ex)
    if last_error:
        raise RuntimeError(f'Falha ao baixar arquivo do Supabase Storage: {last_error}')
    return None

def _materialize_supabase_object(bucket: str, storage_path: str, local_path: Path) -> Optional[Path]:
    if not storage_path:
        return local_path if local_path.exists() else None
    if local_path.exists() and local_path.is_file():
        return local_path
    payload = _supabase_download_object(bucket, storage_path)
    if payload is None:
        return local_path if local_path.exists() else None
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(payload)
    return local_path


def _supabase_public_url(bucket: str, storage_path: str) -> Optional[str]:
    if not (SUPABASE_URL and bucket and storage_path):
        return None
    return f'{SUPABASE_URL}/storage/v1/object/public/{bucket}/{str(storage_path).lstrip("/")}'


def _upload_local_file_to_supabase(bucket: str, storage_path: str, local_path: str | Path, content_type: str = 'application/octet-stream') -> Optional[str]:
    local_file = Path(local_path)
    if not local_file.exists() or not local_file.is_file():
        return None
    payload = local_file.read_bytes()
    return _supabase_upload_bytes(bucket, storage_path, payload, content_type)

def _safe_storage_name(nome: str) -> str:
    nome = str(nome or 'arquivo').strip().replace('\\', '_').replace('/', '_')
    return re.sub(r'[^A-Za-z0-9._-]+', '_', nome) or 'arquivo'

def _guess_content_type(nome: str, tipo: str = '') -> str:
    nome = str(nome or '').lower()
    if nome.endswith('.pdf') or str(tipo or '').upper() == 'PDF':
        return 'application/pdf'
    if nome.endswith('.png'):
        return 'image/png'
    if nome.endswith('.jpg') or nome.endswith('.jpeg'):
        return 'image/jpeg'
    if nome.endswith('.webp'):
        return 'image/webp'
    if nome.endswith('.gif'):
        return 'image/gif'
    if nome.endswith('.bmp'):
        return 'image/bmp'
    return 'application/octet-stream'

def _anexo_local_cache_path(bucket: str, storage_path: str, fallback_name: str) -> Path:
    storage_path = str(storage_path or '').strip('/')
    if storage_path:
        return SUPABASE_CACHE_DIR / bucket / storage_path
    return SUPABASE_CACHE_DIR / bucket / _safe_storage_name(fallback_name)

def _hydrate_storage_fields(item: Optional[dict]) -> Optional[dict]:
    if not item:
        return item
    bucket = str(item.get('bucket') or '').strip()
    storage_path = str(item.get('storage_path') or '').strip()
    caminho = str(item.get('caminho') or '').strip()
    if bucket and storage_path:
        local_path = Path(caminho) if caminho else _anexo_local_cache_path(bucket, storage_path, item.get('nome_salvo') or item.get('nome_original') or 'arquivo')
        resolved = _materialize_supabase_object(bucket, storage_path, local_path)
        if resolved:
            item['caminho'] = str(resolved)
    return item

def _sync_asset_from_supabase(local_name: str, remote_candidates: list[str]) -> bool:
    if not (_supabase_ready() and SUPABASE_ASSETS_ENABLED):
        return False
    destino = LOCAL_ASSETS_DIR / local_name
    if destino.exists() and destino.stat().st_size > 0:
        return True
    for remote_path in remote_candidates:
        try:
            payload = _supabase_download_object(SUPABASE_BUCKET_ASSETS, remote_path)
            if payload:
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_bytes(payload)
                return True
        except Exception:
            continue
    return False

def _bootstrap_assets_from_supabase() -> None:
    assets_map = {
        'logo_fsl.png': ['logo_fsl.png', 'logos/logo_fsl.png', 'assets/logo_fsl.png', 'assets/logos/logo_fsl.png'],
        'logo_app.png': ['logo_app.png', 'logos/logo_app.png', 'assets/logo_app.png', 'assets/logos/logo_app.png'],
        'fundo_fsl.png': ['fundo_fsl.png', 'backgrounds/fundo_fsl.png', 'assets/fundo_fsl.png', 'assets/backgrounds/fundo_fsl.png'],
        'fundo_home.png': ['fundo_home.png', 'backgrounds/fundo_home.png', 'assets/fundo_home.png', 'assets/backgrounds/fundo_home.png'],
        'bg1.jpg': [
            'bg1.jpg', 'bg1.png', 'bg1.webp',
            'backgrounds/bg1.jpg', 'backgrounds/bg1.png', 'backgrounds/bg1.webp',
            'assets/bg1.jpg', 'assets/bg1.png', 'assets/bg1.webp',
            'assets/backgrounds/bg1.jpg', 'assets/backgrounds/bg1.png', 'assets/backgrounds/bg1.webp',
        ],
        'bg2.jpg': [
            'bg2.jpg', 'bg2.png', 'bg2.webp',
            'backgrounds/bg2.jpg', 'backgrounds/bg2.png', 'backgrounds/bg2.webp',
            'assets/bg2.jpg', 'assets/bg2.png', 'assets/bg2.webp',
            'assets/backgrounds/bg2.jpg', 'assets/backgrounds/bg2.png', 'assets/backgrounds/bg2.webp',
        ],
        'bg3.jpg': [
            'bg3.jpg', 'bg3.png', 'bg3.webp',
            'backgrounds/bg3.jpg', 'backgrounds/bg3.png', 'backgrounds/bg3.webp',
            'assets/bg3.jpg', 'assets/bg3.png', 'assets/bg3.webp',
            'assets/backgrounds/bg3.jpg', 'assets/backgrounds/bg3.png', 'assets/backgrounds/bg3.webp',
        ],
    }
    for local_name, candidates in assets_map.items():
        try:
            _sync_asset_from_supabase(local_name, candidates)
        except Exception:
            pass

_bootstrap_assets_from_supabase()


class CompatRow(dict):
    def __init__(self, data: dict, columns: list[str]):
        super().__init__(data)
        self._columns = list(columns)

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._columns[key])
        return super().__getitem__(key)


class CompatCursor:
    def __init__(self, cursor, backend: str, owner=None):
        self._cursor = cursor
        self._backend = backend
        self._owner = owner
        self._last_columns = []

    def _translate(self, query: str):
        sql = str(query)
        if self._backend != 'postgres':
            return sql

        pragma = re.match(r"\s*PRAGMA\s+table_info\(([^)]+)\)\s*", sql, flags=re.I)
        if pragma:
            table_name = pragma.group(1).strip().strip('"').strip("'")
            return (
                "SELECT ordinal_position AS cid, column_name AS name, data_type AS type, "
                "CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull, "
                "column_default AS dflt_value, 0 AS pk "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = '%s' "
                "ORDER BY ordinal_position" % table_name
            )

        sql = sql.replace("SELECT strftime('%Y', 'now', 'localtime')", "SELECT TO_CHAR(CURRENT_TIMESTAMP, 'YYYY')")
        sql = sql.replace("SELECT strftime('%Y', 'now')", "SELECT TO_CHAR(CURRENT_TIMESTAMP, 'YYYY')")
        sql = sql.replace('?', '%s')
        return sql

    def _is_connection_error(self, exc: Exception) -> bool:
        erro_txt = str(exc).lower()
        nome_erro = exc.__class__.__name__.lower()
        chaves_texto = [
            'connection',
            'server closed the connection',
            'terminating connection',
            'could not connect',
            'connection refused',
            'broken pipe',
            'timeout expired',
            'connection timed out',
            'ssl connection has been closed unexpectedly',
            'consuming input failed',
            'sending query failed',
        ]
        chaves_nome = ['operationalerror', 'interfaceerror']
        return any(chave in erro_txt for chave in chaves_texto) or any(chave in nome_erro for chave in chaves_nome)

    def execute(self, query, params=None):
        sql = self._translate(query)
        tried_reconnect = False
        while True:
            try:
                if params is None:
                    self._cursor.execute(sql)
                else:
                    self._cursor.execute(sql, params)
                break
            except Exception as e:
                if self._backend == 'postgres':
                    try:
                        self._cursor.connection.rollback()
                    except Exception:
                        pass
                    if self._owner is not None and not tried_reconnect and self._is_connection_error(e):
                        tried_reconnect = True
                        self._owner._reconnect()
                        self._cursor = self._owner._conn.cursor()
                        continue
                raise
        self._last_columns = [d[0] for d in (self._cursor.description or [])]
        return self

    def executemany(self, query, seq_of_params):
        sql = self._translate(query)
        tried_reconnect = False
        while True:
            try:
                self._cursor.executemany(sql, seq_of_params)
                break
            except Exception as e:
                if self._backend == 'postgres':
                    try:
                        self._cursor.connection.rollback()
                    except Exception:
                        pass
                    if self._owner is not None and not tried_reconnect and self._is_connection_error(e):
                        tried_reconnect = True
                        self._owner._reconnect()
                        self._cursor = self._owner._conn.cursor()
                        continue
                raise
        self._last_columns = [d[0] for d in (self._cursor.description or [])]
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if self._backend == 'sqlite':
            return row
        return CompatRow(dict(row), self._last_columns)

    def fetchall(self):
        rows = self._cursor.fetchall()
        if self._backend == 'sqlite':
            return rows
        return [CompatRow(dict(r), self._last_columns) for r in rows]

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class CompatConnection:
    def __init__(self, backend: str, raw_conn, connector=None):
        self.backend = backend
        self._conn = raw_conn
        self._connector = connector

    def _reconnect(self):
        if not self._connector:
            raise RuntimeError('Conexão indisponível e sem rotina de reconexão.')
        try:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = self._connector()
        except Exception:
            raise

    def _ensure_alive(self):
        if self.backend != 'postgres':
            return
        try:
            closed = getattr(self._conn, 'closed', False)
            if closed:
                self._reconnect()
                return
            cur = self._conn.cursor()
            cur.execute('SELECT 1')
            cur.fetchone()
        except Exception:
            self._reconnect()

    def cursor(self):
        self._ensure_alive()
        return CompatCursor(self._conn.cursor(), self.backend, owner=self)

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._ensure_alive()
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            if self.backend == 'postgres':
                self._reconnect()

    def close(self):
        self._conn.close()


def _new_sqlite_connection():
    raw_conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
    raw_conn.row_factory = sqlite3.Row
    raw_conn.execute('PRAGMA foreign_keys = ON')
    return raw_conn


def _new_postgres_connection():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL não informado para DB_MODE=postgres.')
    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as ex:
        raise RuntimeError('Instale psycopg[binary] para usar Supabase/Postgres.') from ex
    raw = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False, connect_timeout=5)
    try:
        with raw.cursor() as cur:
            cur.execute("SET TIME ZONE 'America/Campo_Grande'")
        raw.commit()
    except Exception:
        try:
            raw.rollback()
        except Exception:
            pass
    return raw


if DB_MODE == 'sqlite':
    raw_conn = _new_sqlite_connection()
    conn = CompatConnection('sqlite', raw_conn, connector=_new_sqlite_connection)
elif DB_MODE in {'postgres', 'postgresql'}:
    raw_conn = _new_postgres_connection()
    conn = CompatConnection('postgres', raw_conn, connector=_new_postgres_connection)
else:
    raise RuntimeError(f'DB_MODE inválido: {DB_MODE}')



def _ensure_postgres_compat():
    if getattr(conn, 'backend', '') != 'postgres':
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE OR REPLACE FUNCTION public.round(double precision, integer)
        RETURNS numeric
        LANGUAGE SQL
        IMMUTABLE
        AS $$
            SELECT pg_catalog.round($1::numeric, $2)
        $$
    """)
    conn.commit()


_ensure_postgres_compat()


def get_connection():
    return conn


def _upper(value) -> str:
    return str(value or '').strip().upper()


def _text(value) -> str:
    return str(value or '').strip()


def _cursor():
    return get_connection().cursor()


def _get_columns(table_name: str) -> set[str]:
    cur = _cursor()
    cur.execute(f'PRAGMA table_info({table_name})')
    return {row[1] for row in cur.fetchall()}


def _ensure_schema():
    cur = _cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ativos (
            id TEXT PRIMARY KEY,
            tag TEXT NOT NULL UNIQUE,
            tag_base TEXT NOT NULL,
            descricao TEXT NOT NULL,
            tipo TEXT NOT NULL,
            parent_id TEXT NULL,
            criticidade INTEGER NULL,
            observacoes TEXT NULL,
            fabricante TEXT NULL,
            modelo TEXT NULL,
            numero_serie TEXT NULL,
            ano_fabricacao INTEGER NULL,
            ativo INTEGER NOT NULL DEFAULT 1,
            pecas_ativas INTEGER NOT NULL DEFAULT 0,
            pecas_json TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(parent_id) REFERENCES ativos(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS anexos (
            id TEXT PRIMARY KEY,
            ativo_id TEXT NOT NULL,
            nome_original TEXT NOT NULL,
            nome_salvo TEXT NOT NULL,
            caminho TEXT NOT NULL,
            tipo TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ativo_id) REFERENCES ativos(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ordens_servico (
            id TEXT PRIMARY KEY,
            numero TEXT NOT NULL UNIQUE,
            origem_tipo TEXT NOT NULL,
            equipamento_id TEXT NOT NULL,
            componente_id TEXT NULL,
            status TEXT NOT NULL DEFAULT 'ABERTA',
            prioridade TEXT NULL,
            tipo_os TEXT NULL,
            descricao TEXT NOT NULL,
            observacoes TEXT NULL,
            justificativa_encerramento TEXT NULL,
            data_abertura TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            data_encerramento TEXT NULL,
            unidade_medidor TEXT NOT NULL DEFAULT 'HORÍMETRO',
            medidor_valor REAL NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(equipamento_id) REFERENCES ativos(id),
            FOREIGN KEY(componente_id) REFERENCES ativos(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS os_atividades (
            id TEXT PRIMARY KEY,
            os_id TEXT NOT NULL,
            sequencia INTEGER NOT NULL DEFAULT 1,
            descricao TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ABERTA',
            observacao TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(os_id) REFERENCES ordens_servico(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS os_apontamentos (
            id TEXT PRIMARY KEY,
            os_id TEXT NOT NULL,
            atividade_id TEXT NOT NULL,
            funcionario_nome TEXT NOT NULL,
            equipe_nome TEXT NULL,
            data_apontamento TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            hora_inicio TEXT NULL,
            hora_fim TEXT NULL,
            duracao_min REAL NOT NULL DEFAULT 0,
            descricao_servico TEXT NULL,
            observacao TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(os_id) REFERENCES ordens_servico(id) ON DELETE CASCADE,
            FOREIGN KEY(atividade_id) REFERENCES os_atividades(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS os_materiais (
            id TEXT PRIMARY KEY,
            os_id TEXT NOT NULL,
            atividade_id TEXT NULL,
            descricao_material TEXT NOT NULL,
            quantidade REAL NOT NULL DEFAULT 1,
            unidade TEXT NULL,
            custo_unitario REAL NOT NULL DEFAULT 0,
            custo_total REAL NOT NULL DEFAULT 0,
            observacao TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(os_id) REFERENCES ordens_servico(id) ON DELETE CASCADE,
            FOREIGN KEY(atividade_id) REFERENCES os_atividades(id) ON DELETE SET NULL
        )
    """)

    conn.commit()

    colunas_anexos = _get_columns('anexos')
    alteracoes_anexos = {
        'bucket': "ALTER TABLE anexos ADD COLUMN bucket TEXT NULL",
        'storage_path': "ALTER TABLE anexos ADD COLUMN storage_path TEXT NULL",
        'url_publica': "ALTER TABLE anexos ADD COLUMN url_publica TEXT NULL",
    }
    for coluna, ddl in alteracoes_anexos.items():
        if coluna not in colunas_anexos:
            cur.execute(ddl)

    colunas = _get_columns('ativos')
    alteracoes = {
        'observacoes': "ALTER TABLE ativos ADD COLUMN observacoes TEXT NULL",
        'fabricante': "ALTER TABLE ativos ADD COLUMN fabricante TEXT NULL",
        'modelo': "ALTER TABLE ativos ADD COLUMN modelo TEXT NULL",
        'numero_serie': "ALTER TABLE ativos ADD COLUMN numero_serie TEXT NULL",
        'ano_fabricacao': "ALTER TABLE ativos ADD COLUMN ano_fabricacao INTEGER NULL",
        'ativo': "ALTER TABLE ativos ADD COLUMN ativo INTEGER NOT NULL DEFAULT 1",
        'pecas_ativas': "ALTER TABLE ativos ADD COLUMN pecas_ativas INTEGER NOT NULL DEFAULT 0",
        'pecas_json': "ALTER TABLE ativos ADD COLUMN pecas_json TEXT NULL",
    }
    for coluna, ddl in alteracoes.items():
        if coluna not in colunas:
            cur.execute(ddl)

    colunas_os = _get_columns('ordens_servico')
    alteracoes_os = {
        'unidade_medidor': "ALTER TABLE ordens_servico ADD COLUMN unidade_medidor TEXT NOT NULL DEFAULT 'HORÍMETRO'",
        'medidor_valor': "ALTER TABLE ordens_servico ADD COLUMN medidor_valor REAL NULL",
    }
    for coluna, ddl in alteracoes_os.items():
        if coluna not in colunas_os:
            cur.execute(ddl)

    conn.commit()


_ensure_schema()


def _migrar_linha_anexo_para_supabase(tabela: str, row: dict, owner_field: str, pasta_storage: str) -> dict:
    item = dict(row or {})
    if not (SUPABASE_UPLOADS_ENABLED and _supabase_ready()):
        return item
    if item.get('bucket') and item.get('storage_path'):
        return item

    caminho = Path(str(item.get('caminho') or '').strip())
    if not caminho.exists() or not caminho.is_file():
        return item

    owner_id = str(item.get(owner_field) or '').strip()
    anexo_id = str(item.get('id') or '').strip()
    nome_salvo = str(item.get('nome_salvo') or '').strip() or f'{anexo_id or uuid.uuid4()}{caminho.suffix.lower()}'
    if not owner_id or not anexo_id:
        return item

    storage_path = f"{pasta_storage}/{owner_id}/{nome_salvo}"
    try:
        _upload_local_file_to_supabase(
            SUPABASE_BUCKET_UPLOADS,
            storage_path,
            caminho,
            _guess_content_type(item.get('nome_original') or nome_salvo, item.get('tipo') or ''),
        )
        url_publica = _supabase_public_url(SUPABASE_BUCKET_UPLOADS, storage_path)
        cur = _cursor()
        cur.execute(
            f"UPDATE {tabela} SET bucket = ?, storage_path = ?, url_publica = ? WHERE id = ?",
            (SUPABASE_BUCKET_UPLOADS, storage_path, url_publica, anexo_id),
        )
        conn.commit()
        item['bucket'] = SUPABASE_BUCKET_UPLOADS
        item['storage_path'] = storage_path
        item['url_publica'] = url_publica
        return item
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return item


def _migrar_assets_locais_para_supabase() -> None:
    if not (SUPABASE_ASSETS_ENABLED and _supabase_ready()):
        return
    assets_map = {
        'logo_fsl.png': 'logos/logo_fsl.png',
        'logo_app.png': 'logos/logo_app.png',
        'fundo_fsl.png': 'backgrounds/fundo_fsl.png',
        'fundo_home.png': 'backgrounds/fundo_home.png',
    }
    for local_name, remote_path in assets_map.items():
        try:
            local_file = LOCAL_ASSETS_DIR / local_name
            if local_file.exists() and local_file.is_file():
                _upload_local_file_to_supabase(
                    SUPABASE_BUCKET_ASSETS,
                    remote_path,
                    local_file,
                    _guess_content_type(local_name),
                )
        except Exception:
            pass


def _migrar_anexos_legados_para_supabase() -> None:
    if not (SUPABASE_UPLOADS_ENABLED and _supabase_ready()):
        return
    try:
        cur = _cursor()
        cur.execute("SELECT * FROM anexos WHERE COALESCE(bucket, '') = '' OR COALESCE(storage_path, '') = ''")
        for row in cur.fetchall():
            _migrar_linha_anexo_para_supabase('anexos', dict(row), 'ativo_id', SUPABASE_STORAGE_FOLDER_ATIVOS)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    try:
        cur = _cursor()
        cur.execute("SELECT * FROM os_anexos WHERE COALESCE(bucket, '') = '' OR COALESCE(storage_path, '') = ''")
        for row in cur.fetchall():
            _migrar_linha_anexo_para_supabase('os_anexos', dict(row), 'os_id', SUPABASE_STORAGE_FOLDER_OS)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


_migrar_assets_locais_para_supabase()
_migrar_anexos_legados_para_supabase()


def _get_ativo_row(ativo_id: str):
    cur = _cursor()
    cur.execute("SELECT * FROM ativos WHERE id = ?", (ativo_id,))
    return cur.fetchone()


def _get_parent_row(parent_id: Optional[str]):
    if not parent_id:
        return None
    cur = _cursor()
    cur.execute("SELECT * FROM ativos WHERE id = ?", (parent_id,))
    return cur.fetchone()


def _parse_pecas(valor: Any):
    if not valor:
        return []
    try:
        return json.loads(valor)
    except Exception:
        return []


def _dump_pecas(valor: Any) -> str:
    if not valor:
        return '[]'
    if isinstance(valor, str):
        try:
            json.loads(valor)
            return valor
        except Exception:
            return '[]'
    return json.dumps(valor, ensure_ascii=False)


def _montar_tag_final(tag_base: str, tipo: str, parent_row):
    tag_base = _upper(tag_base)
    tipo = _upper(tipo)

    if tipo == 'COMPONENTE':
        if not parent_row:
            raise ValueError('COMPONENTE deve possuir pai.')
        parent_tag = _upper(parent_row['tag'])
        return f'{tag_base} [{parent_tag}]'

    return tag_base


def _validar_regras_hierarquia(tipo: str, parent_row):
    tipo = _upper(tipo)

    if tipo == 'LOCAL':
        if parent_row and _upper(parent_row['tipo']) != 'LOCAL':
            raise ValueError('LOCAL só pode ter pai do tipo LOCAL.')
        return

    if tipo == 'EQUIPAMENTO':
        if not parent_row:
            raise ValueError('EQUIPAMENTO deve possuir pai do tipo LOCAL.')
        if _upper(parent_row['tipo']) != 'LOCAL':
            raise ValueError('EQUIPAMENTO só pode ter pai do tipo LOCAL.')
        return

    if tipo == 'COMPONENTE':
        if not parent_row:
            raise ValueError('COMPONENTE deve possuir pai do tipo EQUIPAMENTO.')
        if _upper(parent_row['tipo']) != 'EQUIPAMENTO':
            raise ValueError('COMPONENTE só pode ter pai do tipo EQUIPAMENTO.')
        return

    raise ValueError('Tipo inválido.')


def _validar_local_principal(tipo: str, parent_id: Optional[str], ignorar_id: Optional[str] = None):
    tipo = _upper(tipo)

    if tipo != 'LOCAL' or parent_id:
        return

    cur = _cursor()
    if ignorar_id:
        cur.execute("""
            SELECT COUNT(*) AS total
            FROM ativos
            WHERE tipo = 'LOCAL'
              AND parent_id IS NULL
              AND id <> ?
        """, (ignorar_id,))
    else:
        cur.execute("""
            SELECT COUNT(*) AS total
            FROM ativos
            WHERE tipo = 'LOCAL'
              AND parent_id IS NULL
        """)

    total = cur.fetchone()[0]
    if total > 0:
        raise ValueError('Já existe um LOCAL PRINCIPAL cadastrado.')


def _validar_tag_unica(tag_final: str, ignorar_id: Optional[str] = None):
    cur = _cursor()

    if ignorar_id:
        cur.execute(
            "SELECT id FROM ativos WHERE tag = ? AND id <> ?",
            (tag_final, ignorar_id),
        )
    else:
        cur.execute("SELECT id FROM ativos WHERE tag = ?", (tag_final,))

    if cur.fetchone():
        raise ValueError(f'TAG já existe no sistema: {tag_final}')


def _sanear_campos_por_tipo(
    tipo: str,
    criticidade: Optional[int],
    fabricante: Optional[str],
    modelo: Optional[str],
    numero_serie: Optional[str],
    ano_fabricacao: Optional[int],
    ativo: bool,
    pecas_ativas: bool,
    pecas_json: Any,
):
    tipo = _upper(tipo)
    fabricante = _upper(fabricante)
    modelo = _upper(modelo)
    numero_serie = _upper(numero_serie)
    pecas_json = _dump_pecas(pecas_json)

    if tipo == 'LOCAL':
        criticidade = None
        fabricante = ''
        modelo = ''
        numero_serie = ''
        ano_fabricacao = None
        ativo = 1
        pecas_ativas = 0
        pecas_json = '[]'

    elif tipo == 'EQUIPAMENTO':
        ativo = 1 if ativo else 0
        pecas_ativas = 0
        pecas_json = '[]'

    elif tipo == 'COMPONENTE':
        ano_fabricacao = None
        ativo = 1
        pecas_ativas = 1 if pecas_ativas else 0

    else:
        raise ValueError('Tipo inválido.')

    if criticidade in ('', None):
        criticidade = None
    else:
        criticidade = int(criticidade)

    if ano_fabricacao in ('', None):
        ano_fabricacao = None
    else:
        ano_fabricacao = int(ano_fabricacao)

    return criticidade, fabricante, modelo, numero_serie, ano_fabricacao, ativo, pecas_ativas, pecas_json


def listar_ativos():
    cur = _cursor()
    cur.execute("""
        SELECT
            a.*,
            p.tag AS parent_tag,
            p.descricao AS parent_descricao,
            CASE
                WHEN UPPER(COALESCE(a.tipo, '')) = 'COMPONENTE' THEN COALESCE(osc.qtd_abertas, 0)
                WHEN UPPER(COALESCE(a.tipo, '')) = 'EQUIPAMENTO' THEN COALESCE(ose.qtd_abertas, 0)
                ELSE 0
            END AS qtd_os_abertas
        FROM ativos a
        LEFT JOIN ativos p ON p.id = a.parent_id
        LEFT JOIN (
            SELECT componente_id AS ativo_id, COUNT(*) AS qtd_abertas
            FROM ordens_servico
            WHERE componente_id IS NOT NULL
              AND UPPER(COALESCE(status, '')) IN ('ABERTA', 'EM EXECUÇÃO')
            GROUP BY componente_id
        ) osc ON osc.ativo_id = a.id
        LEFT JOIN (
            SELECT equipamento_id AS ativo_id, COUNT(*) AS qtd_abertas
            FROM ordens_servico
            WHERE equipamento_id IS NOT NULL
              AND UPPER(COALESCE(status, '')) IN ('ABERTA', 'EM EXECUÇÃO')
            GROUP BY equipamento_id
        ) ose ON ose.ativo_id = a.id
        ORDER BY a.tag
    """)
    resultado = []
    for row in cur.fetchall():
        item = dict(row)
        item['ativo'] = bool(item.get('ativo', 1))
        item['pecas_json'] = _parse_pecas(item.get('pecas_json'))
        item['pecas_ativas'] = bool(item.get('pecas_ativas'))
        item['qtd_os_abertas'] = int(item.get('qtd_os_abertas') or 0)
        item['tem_os_aberta'] = item['qtd_os_abertas'] > 0
        resultado.append(item)
    return resultado


def get_ativos():
    return listar_ativos()


def get_ativo(ativo_id: str):
    row = _get_ativo_row(ativo_id)
    if not row:
        return None
    item = dict(row)
    item['ativo'] = bool(item.get('ativo', 1))
    item['pecas_json'] = _parse_pecas(item.get('pecas_json'))
    item['pecas_ativas'] = bool(item.get('pecas_ativas'))
    return item


def listar_pais_possiveis(tipo: str, ignorar_id: Optional[str] = None):
    tipo = _upper(tipo)
    cur = _cursor()

    if tipo == 'LOCAL':
        tipo_pai = 'LOCAL'
    elif tipo == 'EQUIPAMENTO':
        tipo_pai = 'LOCAL'
    elif tipo == 'COMPONENTE':
        tipo_pai = 'EQUIPAMENTO'
    else:
        return []

    sql = """
        SELECT id, tag, descricao, tipo
        FROM ativos
        WHERE tipo = ?
    """
    params = [tipo_pai]

    if ignorar_id:
        sql += " AND id <> ?"
        params.append(ignorar_id)

    sql += " ORDER BY tag"
    cur.execute(sql, tuple(params))
    return [dict(row) for row in cur.fetchall()]


def criar_ativo(
    tag_base: str,
    descricao: str,
    tipo: str,
    parent_id: Optional[str] = None,
    criticidade: Optional[int] = None,
    observacoes: Optional[str] = None,
    fabricante: Optional[str] = None,
    modelo: Optional[str] = None,
    numero_serie: Optional[str] = None,
    ano_fabricacao: Optional[int] = None,
    ativo: bool = True,
    pecas_ativas: bool = False,
    pecas_json: Any = None,
):
    tipo = _upper(tipo)
    tag_base = _upper(tag_base)
    descricao = _upper(descricao)
    observacoes = _text(observacoes)
    parent_id = parent_id or None

    if not tag_base:
        raise ValueError('Informe a TAG.')
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO.')

    parent_row = _get_parent_row(parent_id)

    _validar_regras_hierarquia(tipo, parent_row)
    _validar_local_principal(tipo, parent_id)

    criticidade, fabricante, modelo, numero_serie, ano_fabricacao, ativo, pecas_ativas, pecas_json = _sanear_campos_por_tipo(
        tipo, criticidade, fabricante, modelo, numero_serie, ano_fabricacao, ativo, pecas_ativas, pecas_json
    )

    tag_final = _montar_tag_final(tag_base, tipo, parent_row)
    _validar_tag_unica(tag_final)

    ativo_id = str(uuid.uuid4())
    cur = _cursor()
    cur.execute("""
        INSERT INTO ativos (
            id, tag, tag_base, descricao, tipo, parent_id, criticidade,
            observacoes, fabricante, modelo, numero_serie, ano_fabricacao,
            ativo, pecas_ativas, pecas_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        ativo_id,
        tag_final,
        tag_base,
        descricao,
        tipo,
        parent_id,
        criticidade,
        observacoes,
        fabricante,
        modelo,
        numero_serie,
        ano_fabricacao,
        ativo,
        pecas_ativas,
        pecas_json,
    ))
    conn.commit()
    return get_ativo(ativo_id)


def atualizar_ativo(
    ativo_id: str,
    tag_base: str,
    descricao: str,
    parent_id: Optional[str] = None,
    criticidade: Optional[int] = None,
    observacoes: Optional[str] = None,
    fabricante: Optional[str] = None,
    modelo: Optional[str] = None,
    numero_serie: Optional[str] = None,
    ano_fabricacao: Optional[int] = None,
    ativo: bool = True,
    pecas_ativas: bool = False,
    pecas_json: Any = None,
):
    atual = _get_ativo_row(ativo_id)
    if not atual:
        raise ValueError('Ativo não encontrado.')

    tipo = _upper(atual['tipo'])
    tag_base = _upper(tag_base)
    descricao = _upper(descricao)
    observacoes = _text(observacoes)
    parent_id = parent_id or None

    if not tag_base:
        raise ValueError('Informe a TAG.')
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO.')

    parent_row = _get_parent_row(parent_id)

    _validar_regras_hierarquia(tipo, parent_row)
    _validar_local_principal(tipo, parent_id, ignorar_id=ativo_id)

    criticidade, fabricante, modelo, numero_serie, ano_fabricacao, ativo, pecas_ativas, pecas_json = _sanear_campos_por_tipo(
        tipo, criticidade, fabricante, modelo, numero_serie, ano_fabricacao, ativo, pecas_ativas, pecas_json
    )

    tag_final = _montar_tag_final(tag_base, tipo, parent_row)
    _validar_tag_unica(tag_final, ignorar_id=ativo_id)

    cur = _cursor()
    cur.execute("""
        UPDATE ativos
        SET tag = ?,
            tag_base = ?,
            descricao = ?,
            parent_id = ?,
            criticidade = ?,
            observacoes = ?,
            fabricante = ?,
            modelo = ?,
            numero_serie = ?,
            ano_fabricacao = ?,
            ativo = ?,
            pecas_ativas = ?,
            pecas_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        tag_final,
        tag_base,
        descricao,
        parent_id,
        criticidade,
        observacoes,
        fabricante,
        modelo,
        numero_serie,
        ano_fabricacao,
        ativo,
        pecas_ativas,
        pecas_json,
        ativo_id,
    ))
    conn.commit()
    return get_ativo(ativo_id)


def excluir_ativo(ativo_id: str):
    cur = _cursor()

    cur.execute("SELECT COUNT(*) FROM ativos WHERE parent_id = ?", (ativo_id,))
    total_filhos = cur.fetchone()[0]
    if total_filhos > 0:
        raise ValueError('Não é permitido excluir este ativo porque ele possui filhos.')

    anexos = listar_anexos(ativo_id)

    cur.execute("DELETE FROM ativos WHERE id = ?", (ativo_id,))
    conn.commit()

    for anexo in anexos:
        caminho = anexo.get('caminho')
        if caminho and os.path.exists(caminho):
            try:
                os.remove(caminho)
            except Exception:
                pass

    pasta_ativo = UPLOAD_ATIVOS_DIR / ativo_id
    if pasta_ativo.exists():
        shutil.rmtree(pasta_ativo, ignore_errors=True)


def listar_anexos(ativo_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT *
        FROM anexos
        WHERE ativo_id = ?
        ORDER BY created_at DESC, nome_original
    """, (ativo_id,))
    itens = []
    for row in cur.fetchall():
        item = _migrar_linha_anexo_para_supabase('anexos', dict(row), 'ativo_id', SUPABASE_STORAGE_FOLDER_ATIVOS)
        itens.append(_hydrate_storage_fields(item))
    return itens

def get_anexo(anexo_id: str):
    cur = _cursor()
    cur.execute("SELECT * FROM anexos WHERE id = ?", (anexo_id,))
    row = cur.fetchone()
    if row:
        item = _migrar_linha_anexo_para_supabase('anexos', dict(row), 'ativo_id', SUPABASE_STORAGE_FOLDER_ATIVOS)
        return _hydrate_storage_fields(item)

    cur.execute("SELECT * FROM os_anexos WHERE id = ?", (anexo_id,))
    row = cur.fetchone()
    if row:
        item = _migrar_linha_anexo_para_supabase('os_anexos', dict(row), 'os_id', SUPABASE_STORAGE_FOLDER_OS)
        return _hydrate_storage_fields(item)
    return None

def adicionar_anexo(ativo_id: str, origem_path: str, nome_original: str):
    ativo = _get_ativo_row(ativo_id)
    if not ativo:
        raise ValueError('Ativo não encontrado para anexar arquivo.')

    extensao = Path(nome_original).suffix.lower()
    if extensao in ['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif']:
        tipo = 'FOTO'
    elif extensao == '.pdf':
        tipo = 'PDF'
    else:
        raise ValueError('Tipo de arquivo inválido. Envie apenas imagens ou PDF.')

    anexo_id = str(uuid.uuid4())
    pasta_ativo = UPLOAD_ATIVOS_DIR / ativo_id
    pasta_ativo.mkdir(parents=True, exist_ok=True)

    nome_salvo = f'{anexo_id}{extensao}'
    destino = pasta_ativo / nome_salvo
    shutil.copy2(origem_path, destino)

    bucket = None
    storage_path = None
    url_publica = None
    if SUPABASE_UPLOADS_ENABLED and _supabase_ready():
        storage_path = f"{SUPABASE_STORAGE_FOLDER_ATIVOS}/{ativo_id}/{nome_salvo}"
        payload = Path(origem_path).read_bytes()
        _supabase_upload_bytes(SUPABASE_BUCKET_UPLOADS, storage_path, payload, _guess_content_type(nome_original, tipo))
        bucket = SUPABASE_BUCKET_UPLOADS
        url_publica = f'{SUPABASE_URL}/storage/v1/object/public/{bucket}/{storage_path}'

    cur = _cursor()
    cur.execute("""
        INSERT INTO anexos (
            id, ativo_id, nome_original, nome_salvo, caminho, tipo, bucket, storage_path, url_publica, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        anexo_id,
        ativo_id,
        nome_original,
        nome_salvo,
        str(destino),
        tipo,
        bucket,
        storage_path,
        url_publica,
    ))
    conn.commit()

    return _hydrate_storage_fields({
        'id': anexo_id,
        'ativo_id': ativo_id,
        'nome_original': nome_original,
        'nome_salvo': nome_salvo,
        'caminho': str(destino),
        'tipo': tipo,
        'bucket': bucket,
        'storage_path': storage_path,
        'url_publica': url_publica,
    })

def remover_anexo(anexo_id: str):
    cur = _cursor()
    cur.execute("SELECT * FROM anexos WHERE id = ?", (anexo_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Anexo não encontrado.')

    item = dict(row)
    caminho = item.get('caminho')
    bucket = item.get('bucket')
    storage_path = item.get('storage_path')

    cur.execute("DELETE FROM anexos WHERE id = ?", (anexo_id,))
    conn.commit()

    if bucket and storage_path:
        _supabase_delete_object(bucket, storage_path)

    if caminho and os.path.exists(caminho):
        try:
            os.remove(caminho)
        except Exception:
            pass

def contar_filhos(ativo_id: str) -> int:
    cur = _cursor()
    cur.execute("SELECT COUNT(*) FROM ativos WHERE parent_id = ?", (ativo_id,))
    return int(cur.fetchone()[0])


# =========================
# OS / ATIVIDADES / MATERIAIS / APONTAMENTOS
# =========================

def _agora_sql() -> str:
    cur = _cursor()
    cur.execute("SELECT CURRENT_TIMESTAMP")
    return str(cur.fetchone()[0])


def _get_os_row(os_id: str):
    cur = _cursor()
    cur.execute("SELECT * FROM ordens_servico WHERE id = ?", (os_id,))
    return cur.fetchone()


def _get_atividade_row(atividade_id: str):
    cur = _cursor()
    cur.execute("SELECT * FROM os_atividades WHERE id = ?", (atividade_id,))
    return cur.fetchone()


def _resolver_alvo_os(alvo_ativo_id: str):
    alvo = _get_ativo_row(alvo_ativo_id)
    if not alvo:
        raise ValueError('Ativo alvo da OS não encontrado.')

    tipo = _upper(alvo['tipo'])
    if tipo == 'EQUIPAMENTO':
        return {
            'origem_tipo': 'EQUIPAMENTO',
            'equipamento_id': str(alvo['id']),
            'componente_id': None,
            'alvo_tag': _upper(alvo['tag']),
            'alvo_descricao': _upper(alvo['descricao']),
        }

    if tipo == 'COMPONENTE':
        parent = _get_parent_row(alvo['parent_id'])
        if not parent or _upper(parent['tipo']) != 'EQUIPAMENTO':
            raise ValueError('COMPONENTE da OS precisa possuir EQUIPAMENTO pai válido.')
        return {
            'origem_tipo': 'COMPONENTE',
            'equipamento_id': str(parent['id']),
            'componente_id': str(alvo['id']),
            'alvo_tag': _upper(alvo['tag']),
            'alvo_descricao': _upper(alvo['descricao']),
        }

    raise ValueError('OS só pode ser aberta para EQUIPAMENTO ou COMPONENTE.')


def listar_alvos_os():
    cur = _cursor()
    cur.execute("""
        SELECT id, tag, descricao, tipo, parent_id
        FROM ativos
        WHERE tipo IN ('EQUIPAMENTO', 'COMPONENTE')
        ORDER BY tag
    """)
    resultado = []
    for row in cur.fetchall():
        item = dict(row)
        if item['tipo'] == 'COMPONENTE' and item.get('parent_id'):
            parent = _get_parent_row(item['parent_id'])
            item['equipamento_tag'] = _upper(parent['tag']) if parent else ''
        else:
            item['equipamento_tag'] = _upper(item['tag'])
        resultado.append(item)
    return resultado


def proximo_numero_os() -> str:
    cur = _cursor()
    cur.execute("SELECT strftime('%Y', 'now', 'localtime')")
    ano = str(cur.fetchone()[0])
    cur.execute("""
        SELECT numero
        FROM ordens_servico
        WHERE numero LIKE ?
        ORDER BY numero DESC
        LIMIT 1
    """, (f'OS-{ano}-%',))
    row = cur.fetchone()
    if not row or not row['numero']:
        sequencia = 1
    else:
        try:
            sequencia = int(str(row['numero']).split('-')[-1]) + 1
        except Exception:
            sequencia = 1
    return f'OS-{ano}-{sequencia:04d}'

def listar_os(busca: Optional[str] = None, status: Optional[str] = None):
    busca = _upper(busca)
    status = _upper(status)

    cur = _cursor()
    cur.execute("""
        SELECT
            os.*,
            eq.tag AS equipamento_tag,
            eq.descricao AS equipamento_descricao,
            cp.tag AS componente_tag,
            cp.descricao AS componente_descricao,
            (
                SELECT COUNT(*)
                FROM os_atividades a
                WHERE a.os_id = os.id
            ) AS total_atividades,
            (
                SELECT COUNT(*)
                FROM os_atividades a
                WHERE a.os_id = os.id
                  AND a.status = 'CONCLUÍDA'
            ) AS atividades_concluidas,
            (
                SELECT COALESCE(SUM(m.custo_total), 0)
                FROM os_materiais m
                WHERE m.os_id = os.id
            ) AS custo_materiais,
            (
                SELECT COALESCE(SUM(ap.duracao_min), 0)
                FROM os_apontamentos ap
                WHERE ap.os_id = os.id
            ) AS duracao_total_min
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        LEFT JOIN ativos cp ON cp.id = os.componente_id
        ORDER BY os.created_at DESC, os.numero DESC
    """)
    rows = [dict(row) for row in cur.fetchall()]

    if status:
        rows = [row for row in rows if _upper(row.get('status')) == status]

    if busca:
        filtrados = []
        for row in rows:
            texto = ' '.join([
                str(row.get('numero') or ''),
                str(row.get('descricao') or ''),
                str(row.get('tipo_os') or ''),
                str(row.get('prioridade') or ''),
                str(row.get('status') or ''),
                str(row.get('equipamento_tag') or ''),
                str(row.get('equipamento_descricao') or ''),
                str(row.get('componente_tag') or ''),
                str(row.get('componente_descricao') or ''),
            ])
            if busca in _upper(texto):
                filtrados.append(row)
        rows = filtrados

    return rows


def get_os(os_id: str):
    row = _get_os_row(os_id)
    if not row:
        return None
    item = dict(row)
    equipamento = get_ativo(item['equipamento_id']) if item.get('equipamento_id') else None
    componente = get_ativo(item['componente_id']) if item.get('componente_id') else None
    item['equipamento'] = equipamento
    item['componente'] = componente
    item['total_atividades'] = len(listar_os_atividades(os_id))
    item['total_materiais'] = len(listar_os_materiais(os_id))
    item['total_apontamentos'] = len(listar_os_apontamentos(os_id))
    return item


def criar_os(
    alvo_ativo_id: str,
    descricao: str,
    tipo_os: Optional[str] = None,
    prioridade: Optional[str] = None,
    observacoes: Optional[str] = None,
    numero: Optional[str] = None,
    data_abertura: Optional[str] = None,
    unidade_medidor: Optional[str] = None,
    medidor_valor: Optional[float] = None,
):
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da OS.')

    alvo = _resolver_alvo_os(alvo_ativo_id)
    os_id = str(uuid.uuid4())
    numero = _upper(numero) or proximo_numero_os()

    cur = _cursor()
    cur.execute("""
        INSERT INTO ordens_servico (
            id, numero, origem_tipo, equipamento_id, componente_id,
            status, prioridade, tipo_os, descricao, observacoes,
            data_abertura, unidade_medidor, medidor_valor, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'ABERTA', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        os_id,
        numero,
        alvo['origem_tipo'],
        alvo['equipamento_id'],
        alvo['componente_id'],
        _upper(prioridade),
        _upper(tipo_os),
        descricao,
        _text(observacoes),
        _text(data_abertura) or _agora_sql()[:10],
        _upper(unidade_medidor) or 'HORÍMETRO',
        float(medidor_valor) if medidor_valor not in ('', None) else None,
    ))
    conn.commit()
    return get_os(os_id)


def atualizar_os(
    os_id: str,
    alvo_ativo_id: str,
    descricao: str,
    tipo_os: Optional[str] = None,
    prioridade: Optional[str] = None,
    observacoes: Optional[str] = None,
    status: Optional[str] = None,
    justificativa_encerramento: Optional[str] = None,
    data_abertura: Optional[str] = None,
    unidade_medidor: Optional[str] = None,
    medidor_valor: Optional[float] = None,
):
    atual = _get_os_row(os_id)
    if not atual:
        raise ValueError('OS não encontrada.')

    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da OS.')

    alvo = _resolver_alvo_os(alvo_ativo_id)
    novo_status = _upper(status or atual['status'] or 'ABERTA')
    justificativa_encerramento = _text(justificativa_encerramento)

    if novo_status == 'ENCERRADA':
        validar_encerramento_os(os_id, justificativa_encerramento)

    data_encerramento = _agora_sql() if novo_status == 'ENCERRADA' else None

    cur = _cursor()
    cur.execute("""
        UPDATE ordens_servico
        SET origem_tipo = ?,
            equipamento_id = ?,
            componente_id = ?,
            descricao = ?,
            tipo_os = ?,
            prioridade = ?,
            observacoes = ?,
            data_abertura = ?,
            unidade_medidor = ?,
            medidor_valor = ?,
            status = ?,
            justificativa_encerramento = ?,
            data_encerramento = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        alvo['origem_tipo'],
        alvo['equipamento_id'],
        alvo['componente_id'],
        descricao,
        _upper(tipo_os),
        _upper(prioridade),
        _text(observacoes),
        _text(data_abertura) or _text(atual['data_abertura']),
        _upper(unidade_medidor) or _upper(atual['unidade_medidor']) or 'HORÍMETRO',
        float(medidor_valor) if medidor_valor not in ('', None) else atual['medidor_valor'],
        novo_status,
        justificativa_encerramento,
        data_encerramento,
        os_id,
    ))
    conn.commit()
    return get_os(os_id)


def excluir_os(os_id: str):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')
    cur = _cursor()
    cur.execute("DELETE FROM ordens_servico WHERE id = ?", (os_id,))
    conn.commit()


def validar_encerramento_os(os_id: str, justificativa_encerramento: Optional[str] = None):
    atividades = listar_os_atividades(os_id)
    if not atividades:
        if not _text(justificativa_encerramento):
            raise ValueError('Para ENCERRAR sem atividades, informe JUSTIFICATIVA.')
        return True

    pendentes = [a for a in atividades if _upper(a.get('status')) != 'CONCLUÍDA']
    if pendentes and not _text(justificativa_encerramento):
        raise ValueError('A OS só encerra com atividades concluídas ou JUSTIFICATIVA.')
    return True


def listar_os_atividades(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT
            a.*,
            (
                SELECT COUNT(*)
                FROM os_apontamentos ap
                WHERE ap.atividade_id = a.id
            ) AS total_apontamentos
        FROM os_atividades a
        WHERE a.os_id = ?
        ORDER BY a.sequencia, a.created_at
    """, (os_id,))
    return [dict(row) for row in cur.fetchall()]


def criar_os_atividade(os_id: str, descricao: str, observacao: Optional[str] = None):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')

    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da atividade.')

    cur = _cursor()
    cur.execute("SELECT COALESCE(MAX(sequencia), 0) + 1 FROM os_atividades WHERE os_id = ?", (os_id,))
    sequencia = int(cur.fetchone()[0])
    atividade_id = str(uuid.uuid4())

    cur.execute("""
        INSERT INTO os_atividades (
            id, os_id, sequencia, descricao, status, observacao, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'ABERTA', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        atividade_id,
        os_id,
        sequencia,
        descricao,
        _text(observacao),
    ))
    conn.commit()
    return dict(_get_atividade_row(atividade_id))


def atualizar_os_atividade(
    atividade_id: str,
    descricao: str,
    observacao: Optional[str] = None,
    status: Optional[str] = None,
):
    atual = _get_atividade_row(atividade_id)
    if not atual:
        raise ValueError('Atividade não encontrada.')

    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da atividade.')

    novo_status = _upper(status or atual['status'] or 'ABERTA')
    if novo_status == 'CONCLUÍDA' and contar_apontamentos_atividade(atividade_id) <= 0:
        raise ValueError('ATIVIDADE só conclui com APONTAMENTO.')

    cur = _cursor()
    cur.execute("""
        UPDATE os_atividades
        SET descricao = ?,
            observacao = ?,
            status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        descricao,
        _text(observacao),
        novo_status,
        atividade_id,
    ))
    conn.commit()
    return dict(_get_atividade_row(atividade_id))


def excluir_os_atividade(atividade_id: str):
    atual = _get_atividade_row(atividade_id)
    if not atual:
        raise ValueError('Atividade não encontrada.')
    if contar_apontamentos_atividade(atividade_id) > 0:
        raise ValueError('Não é permitido excluir atividade com APONTAMENTO.')
    cur = _cursor()
    cur.execute("DELETE FROM os_atividades WHERE id = ?", (atividade_id,))
    conn.commit()


def contar_apontamentos_atividade(atividade_id: str) -> int:
    cur = _cursor()
    cur.execute("SELECT COUNT(*) FROM os_apontamentos WHERE atividade_id = ?", (atividade_id,))
    return int(cur.fetchone()[0])


def listar_os_apontamentos(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT ap.*, atv.descricao AS atividade_descricao
        FROM os_apontamentos ap
        JOIN os_atividades atv ON atv.id = ap.atividade_id
        WHERE ap.os_id = ?
        ORDER BY ap.created_at DESC
    """, (os_id,))
    return [dict(row) for row in cur.fetchall()]


def criar_os_apontamento(
    os_id: str,
    atividade_id: str,
    funcionario_nome: str,
    equipe_nome: Optional[str] = None,
    hora_inicio: Optional[str] = None,
    hora_fim: Optional[str] = None,
    duracao_min: Optional[float] = None,
    descricao_servico: Optional[str] = None,
    observacao: Optional[str] = None,
    data_apontamento: Optional[str] = None,
):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')
    atividade = _get_atividade_row(atividade_id)
    if not atividade or str(atividade['os_id']) != str(os_id):
        raise ValueError('Atividade inválida para esta OS.')

    funcionario_nome = _upper(funcionario_nome)
    if not funcionario_nome:
        raise ValueError('Informe o FUNCIONÁRIO do apontamento.')

    try:
        duracao_min = float(duracao_min or 0)
    except Exception:
        duracao_min = 0
    if duracao_min <= 0:
        raise ValueError('Informe uma DURAÇÃO válida em minutos.')

    ap_id = str(uuid.uuid4())
    cur = _cursor()
    cur.execute("""
        INSERT INTO os_apontamentos (
            id, os_id, atividade_id, funcionario_nome, equipe_nome,
            data_apontamento, hora_inicio, hora_fim, duracao_min,
            descricao_servico, observacao, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        ap_id,
        os_id,
        atividade_id,
        funcionario_nome,
        _upper(equipe_nome),
        _text(data_apontamento) or _agora_sql()[:10],
        _text(hora_inicio),
        _text(hora_fim),
        duracao_min,
        _text(descricao_servico),
        _text(observacao),
    ))
    if _upper(atividade['status']) == 'ABERTA':
        cur.execute("""
            UPDATE os_atividades
            SET status = 'EM EXECUÇÃO', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (atividade_id,))
    conn.commit()
    return ap_id


def excluir_os_apontamento(apontamento_id: str):
    cur = _cursor()
    cur.execute("DELETE FROM os_apontamentos WHERE id = ?", (apontamento_id,))
    conn.commit()


def listar_os_materiais(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT
            m.*,
            a.descricao AS atividade_descricao
        FROM os_materiais m
        LEFT JOIN os_atividades a ON a.id = m.atividade_id
        WHERE m.os_id = ?
        ORDER BY m.created_at DESC
    """, (os_id,))
    return [dict(row) for row in cur.fetchall()]


def criar_os_material(
    os_id: str,
    descricao_material: str,
    quantidade: float,
    custo_unitario: float,
    unidade: Optional[str] = None,
    atividade_id: Optional[str] = None,
    observacao: Optional[str] = None,
):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')

    descricao_material = _upper(descricao_material)
    if not descricao_material:
        raise ValueError('Informe o MATERIAL.')

    try:
        quantidade = float(str(quantidade).replace(',', '.'))
    except Exception:
        raise ValueError('Quantidade inválida.')

    try:
        custo_unitario = float(str(custo_unitario).replace(',', '.'))
    except Exception:
        raise ValueError('Custo unitário inválido.')

    if quantidade <= 0:
        raise ValueError('A quantidade deve ser maior que zero.')
    if custo_unitario < 0:
        raise ValueError('O custo unitário não pode ser negativo.')

    if atividade_id:
        atividade = _get_atividade_row(atividade_id)
        if not atividade or str(atividade['os_id']) != str(os_id):
            raise ValueError('Atividade inválida para vincular material.')

    material_id = str(uuid.uuid4())
    custo_total = round(quantidade * custo_unitario, 2)

    cur = _cursor()
    cur.execute("""
        INSERT INTO os_materiais (
            id, os_id, atividade_id, descricao_material,
            quantidade, unidade, custo_unitario, custo_total,
            observacao, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        material_id,
        os_id,
        atividade_id or None,
        descricao_material,
        quantidade,
        _upper(unidade),
        custo_unitario,
        custo_total,
        _text(observacao),
    ))
    conn.commit()
    return material_id


def excluir_os_material(material_id: str):
    cur = _cursor()
    cur.execute("DELETE FROM os_materiais WHERE id = ?", (material_id,))
    conn.commit()


def calcular_totais_os(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT
            COALESCE((SELECT SUM(custo_total) FROM os_materiais WHERE os_id = ?), 0) AS custo_materiais,
            COALESCE((SELECT SUM(duracao_min) FROM os_apontamentos WHERE os_id = ?), 0) AS hh_min
    """, (os_id, os_id))
    row = cur.fetchone()
    return {
        'custo_materiais': float(row['custo_materiais'] or 0),
        'hh_min': float(row['hh_min'] or 0),
        'hh_horas': round(float(row['hh_min'] or 0) / 60, 2),
    }


# =========================
# EQUIPES / ESCALAS / FUNCIONÁRIOS / USUÁRIOS
# =========================

def _slug_username(nome: str) -> str:
    import unicodedata
    nome = str(nome or '').strip().lower()
    nome = ''.join(c for c in unicodedata.normalize('NFD', nome) if unicodedata.category(c) != 'Mn')
    partes = [p for p in nome.replace('-', ' ').split() if p]
    if not partes:
        return 'usuario'
    return f"{partes[0]}{partes[-1]}"


def _ensure_people_schema():
    cur = _cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS equipes (
            id TEXT PRIMARY KEY,
            nome TEXT NOT NULL UNIQUE,
            ativo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS escalas (
            id TEXT PRIMARY KEY,
            nome TEXT NOT NULL UNIQUE,
            ativo INTEGER NOT NULL DEFAULT 1,
            seg_inicio TEXT NULL, seg_fim TEXT NULL,
            ter_inicio TEXT NULL, ter_fim TEXT NULL,
            qua_inicio TEXT NULL, qua_fim TEXT NULL,
            qui_inicio TEXT NULL, qui_fim TEXT NULL,
            sex_inicio TEXT NULL, sex_fim TEXT NULL,
            sab_inicio TEXT NULL, sab_fim TEXT NULL,
            dom_inicio TEXT NULL, dom_fim TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS funcionarios (
            id TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            matricula TEXT NULL,
            equipe_id TEXT NULL,
            escala_id TEXT NULL,
            cargo TEXT NULL,
            custo_mensal_bruto REAL NOT NULL DEFAULT 0,
            carga_horaria_mensal REAL NOT NULL DEFAULT 220,
            custo_hh REAL NOT NULL DEFAULT 0,
            ativo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(equipe_id) REFERENCES equipes(id),
            FOREIGN KEY(escala_id) REFERENCES escalas(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id TEXT PRIMARY KEY,
            funcionario_id TEXT NULL,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            nivel_acesso TEXT NOT NULL DEFAULT 'VISUALIZACAO',
            ativo INTEGER NOT NULL DEFAULT 1,
            deve_trocar_senha INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(funcionario_id) REFERENCES funcionarios(id)
        )
    """)
    # migração de apontamentos
    try:
        cols = _get_columns('os_apontamentos')
        add = {
            'funcionario_id': "ALTER TABLE os_apontamentos ADD COLUMN funcionario_id TEXT NULL",
            'equipe_id': "ALTER TABLE os_apontamentos ADD COLUMN equipe_id TEXT NULL",
            'modo_hora': "ALTER TABLE os_apontamentos ADD COLUMN modo_hora TEXT NULL",
        }
        for c, ddl in add.items():
            if c not in cols:
                cur.execute(ddl)
    except Exception:
        pass
    conn.commit()
    seed_admin_user()


def seed_admin_user():
    cur = _cursor()
    cur.execute("SELECT id FROM usuarios WHERE username = 'admin'")
    if cur.fetchone():
        return
    usuario_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO usuarios (id, funcionario_id, username, password, nivel_acesso, ativo, deve_trocar_senha, created_at, updated_at)
        VALUES (?, NULL, 'admin', '1234', 'ADMIN', 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (usuario_id,))
    conn.commit()


_ensure_people_schema()


def autenticar_usuario(username: str, password: str):
    username = str(username or '').strip().lower()
    password = str(password or '').strip()
    if not username:
        return False, 'Informe o usuário.', None
    cur = _cursor()
    cur.execute("""
        SELECT u.*, f.nome AS nome_funcionario,
               COALESCE(u.nome, f.nome, u.username) AS nome_exibicao
        FROM usuarios u
        LEFT JOIN funcionarios f ON f.id = u.funcionario_id
        WHERE lower(u.username) = ? AND u.ativo = 1
    """, (username,))
    row = cur.fetchone()
    if not row:
        return False, 'Usuário não encontrado.', None
    row = dict(row)
    password_ok = _verify_password(password, row.get('password')) if '_verify_password' in globals() else (str(row.get('password') or '') == password)
    if not password_ok:
        return False, 'Senha incorreta.', None
    if '_needs_password_upgrade' in globals() and _needs_password_upgrade(row.get('password')):
        try:
            alterar_senha_usuario(row.get('id'), password, bool(row.get('deve_trocar_senha', 0)))
            cur.execute("SELECT * FROM usuarios WHERE id = ?", (row.get('id'),))
            row2 = cur.fetchone()
            if row2:
                row.update(dict(row2))
        except Exception:
            pass
    return True, '', row


def alterar_senha_usuario(usuario_id: str, nova_senha: str, deve_trocar: bool = False):
    if not usuario_id:
        raise ValueError('Usuário inválido.')
    if len(str(nova_senha or '').strip()) < 4:
        raise ValueError('A senha deve ter pelo menos 4 caracteres.')
    valor_password = _hash_password(nova_senha) if '_hash_password' in globals() else _text(nova_senha)
    cur = _cursor()
    cur.execute(
        """
        UPDATE usuarios
        SET password = ?, deve_trocar_senha = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (valor_password, 1 if deve_trocar else 0, usuario_id),
    )
    conn.commit()


def listar_equipes():
    cur = _cursor()
    cur.execute("SELECT * FROM equipes ORDER BY nome")
    return [dict(r) for r in cur.fetchall()]


def criar_equipe(nome: str, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da equipe.')
    eq_id = str(uuid.uuid4())
    cur = _cursor()
    cur.execute("INSERT INTO equipes (id, nome, ativo, created_at, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (eq_id, nome, 1 if ativo else 0))
    conn.commit()
    return eq_id


def atualizar_equipe(equipe_id: str, nome: str, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da equipe.')
    cur = _cursor()
    cur.execute("UPDATE equipes SET nome = ?, ativo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (nome, 1 if ativo else 0, equipe_id))
    conn.commit()


def excluir_equipe(equipe_id: str):
    cur = _cursor()
    cur.execute("SELECT COUNT(*) FROM funcionarios WHERE equipe_id = ?", (equipe_id,))
    if int(cur.fetchone()[0] or 0) > 0:
        raise ValueError('Não é possível excluir equipe com funcionários vinculados.')
    cur.execute("DELETE FROM equipes WHERE id = ?", (equipe_id,))
    conn.commit()


def listar_escalas():
    cur = _cursor()
    cur.execute("SELECT * FROM escalas ORDER BY nome")
    return [dict(r) for r in cur.fetchall()]


def criar_escala(nome: str, dias: dict, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da escala.')
    esc_id = str(uuid.uuid4())
    payload = {k: _text(v) or None for k, v in (dias or {}).items()}
    cur = _cursor()
    cur.execute("""
        INSERT INTO escalas (
            id, nome, ativo,
            seg_inicio, seg_fim, ter_inicio, ter_fim, qua_inicio, qua_fim,
            qui_inicio, qui_fim, sex_inicio, sex_fim, sab_inicio, sab_fim,
            dom_inicio, dom_fim, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        esc_id, nome, 1 if ativo else 0,
        payload.get('seg_inicio'), payload.get('seg_fim'), payload.get('ter_inicio'), payload.get('ter_fim'),
        payload.get('qua_inicio'), payload.get('qua_fim'), payload.get('qui_inicio'), payload.get('qui_fim'),
        payload.get('sex_inicio'), payload.get('sex_fim'), payload.get('sab_inicio'), payload.get('sab_fim'),
        payload.get('dom_inicio'), payload.get('dom_fim'),
    ))
    conn.commit()
    return esc_id


def atualizar_escala(escala_id: str, nome: str, dias: dict, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da escala.')
    payload = {k: _text(v) or None for k, v in (dias or {}).items()}
    cur = _cursor()
    cur.execute("""
        UPDATE escalas
        SET nome = ?, ativo = ?,
            seg_inicio = ?, seg_fim = ?, ter_inicio = ?, ter_fim = ?, qua_inicio = ?, qua_fim = ?,
            qui_inicio = ?, qui_fim = ?, sex_inicio = ?, sex_fim = ?, sab_inicio = ?, sab_fim = ?,
            dom_inicio = ?, dom_fim = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        nome, 1 if ativo else 0,
        payload.get('seg_inicio'), payload.get('seg_fim'), payload.get('ter_inicio'), payload.get('ter_fim'),
        payload.get('qua_inicio'), payload.get('qua_fim'), payload.get('qui_inicio'), payload.get('qui_fim'),
        payload.get('sex_inicio'), payload.get('sex_fim'), payload.get('sab_inicio'), payload.get('sab_fim'),
        payload.get('dom_inicio'), payload.get('dom_fim'), escala_id,
    ))
    conn.commit()


def excluir_escala(escala_id: str):
    cur = _cursor()
    cur.execute("SELECT COUNT(*) FROM funcionarios WHERE escala_id = ?", (escala_id,))
    if int(cur.fetchone()[0] or 0) > 0:
        raise ValueError('Não é possível excluir escala com funcionários vinculados.')
    cur.execute("DELETE FROM escalas WHERE id = ?", (escala_id,))
    conn.commit()


def _calc_custo_hh(custo_mensal_bruto, carga_horaria_mensal):
    try:
        custo_mensal_bruto = float(custo_mensal_bruto or 0)
        carga_horaria_mensal = float(carga_horaria_mensal or 0)
    except Exception:
        return 0.0
    if carga_horaria_mensal <= 0:
        return 0.0
    return round(custo_mensal_bruto / carga_horaria_mensal, 2)


def listar_funcionarios(apenas_ativos: bool = False):
    cur = _cursor()
    cur.execute("""
        SELECT f.*, e.nome AS equipe_nome, s.nome AS escala_nome
        FROM funcionarios f
        LEFT JOIN equipes e ON e.id = f.equipe_id
        LEFT JOIN escalas s ON s.id = f.escala_id
        ORDER BY f.nome
    """)
    rows = [dict(r) for r in cur.fetchall()]
    if apenas_ativos:
        rows = [r for r in rows if bool(r.get('ativo', 1))]
    return rows


def get_funcionario(funcionario_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT f.*, e.nome AS equipe_nome, s.nome AS escala_nome
        FROM funcionarios f
        LEFT JOIN equipes e ON e.id = f.equipe_id
        LEFT JOIN escalas s ON s.id = f.escala_id
        WHERE f.id = ?
    """, (funcionario_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def criar_funcionario(nome: str, matricula: str = None, equipe_id: str = None, escala_id: str = None, cargo: str = None,
                      custo_mensal_bruto: float = 0, carga_horaria_mensal: float = 220, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome do funcionário.')
    funcionario_id = str(uuid.uuid4())
    custo_hh = _calc_custo_hh(custo_mensal_bruto, carga_horaria_mensal)
    cur = _cursor()
    cur.execute("""
        INSERT INTO funcionarios (id, nome, matricula, equipe_id, escala_id, cargo, custo_mensal_bruto, carga_horaria_mensal, custo_hh, ativo, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (funcionario_id, nome, _upper(matricula), equipe_id or None, escala_id or None, _upper(cargo), float(custo_mensal_bruto or 0), float(carga_horaria_mensal or 220), custo_hh, 1 if ativo else 0))
    conn.commit()
    return funcionario_id


def atualizar_funcionario(funcionario_id: str, nome: str, matricula: str = None, equipe_id: str = None, escala_id: str = None, cargo: str = None,
                          custo_mensal_bruto: float = 0, carga_horaria_mensal: float = 220, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome do funcionário.')
    custo_hh = _calc_custo_hh(custo_mensal_bruto, carga_horaria_mensal)
    cur = _cursor()
    cur.execute("""
        UPDATE funcionarios
        SET nome = ?, matricula = ?, equipe_id = ?, escala_id = ?, cargo = ?, custo_mensal_bruto = ?, carga_horaria_mensal = ?, custo_hh = ?, ativo = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (nome, _upper(matricula), equipe_id or None, escala_id or None, _upper(cargo), float(custo_mensal_bruto or 0), float(carga_horaria_mensal or 220), custo_hh, 1 if ativo else 0, funcionario_id))
    conn.commit()


def excluir_funcionario(funcionario_id: str):
    cur = _cursor()
    cur.execute("SELECT COUNT(*) FROM usuarios WHERE funcionario_id = ?", (funcionario_id,))
    if int(cur.fetchone()[0] or 0) > 0:
        raise ValueError('Não é possível excluir funcionário com usuário vinculado.')
    cur.execute("DELETE FROM funcionarios WHERE id = ?", (funcionario_id,))
    conn.commit()


def listar_usuarios():
    cur = _cursor()
    cur.execute("""
        SELECT u.*, f.nome AS nome_funcionario, e.nome AS equipe_nome,
               COALESCE(u.nome, f.nome, u.username) AS nome_exibicao
        FROM usuarios u
        LEFT JOIN funcionarios f ON f.id = u.funcionario_id
        LEFT JOIN equipes e ON e.id = f.equipe_id
        ORDER BY u.username
    """)
    return [dict(r) for r in cur.fetchall()]


def sugerir_username_funcionario(funcionario_id: str) -> str:
    func = get_funcionario(funcionario_id)
    if not func:
        raise ValueError('Funcionário não encontrado.')
    base = _slug_username(func.get('nome'))
    username = base
    cur = _cursor()
    i = 1
    while True:
        cur.execute("SELECT id FROM usuarios WHERE lower(username) = ?", (username.lower(),))
        row = cur.fetchone()
        if not row:
            return username
        i += 1
        username = f'{base}{i}'


def criar_usuario(funcionario_id: str, nivel_acesso: str = 'VISUALIZACAO', ativo: bool = True):
    func = get_funcionario(funcionario_id)
    if not func:
        raise ValueError('Funcionário não encontrado.')
    cur = _cursor()
    cur.execute("SELECT id FROM usuarios WHERE funcionario_id = ?", (funcionario_id,))
    if cur.fetchone():
        raise ValueError('Este funcionário já possui usuário.')
    usuario_id = str(uuid.uuid4())
    username = sugerir_username_funcionario(funcionario_id)
    cur.execute("""
        INSERT INTO usuarios (id, funcionario_id, username, password, nivel_acesso, ativo, deve_trocar_senha, created_at, updated_at)
        VALUES (?, ?, ?, 'funsolos1980', ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (usuario_id, funcionario_id, username, _normalize_perfil_acesso(nivel_acesso) or 'VISUALIZACAO', 1 if ativo else 0))
    conn.commit()
    return usuario_id


def atualizar_usuario(usuario_id: str, funcionario_id: str, nivel_acesso: str = 'VISUALIZACAO', ativo: bool = True):
    cur = _cursor()
    cur.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
    atual = cur.fetchone()
    if not atual:
        raise ValueError('Usuário não encontrado.')
    if funcionario_id:
        cur.execute("SELECT id FROM usuarios WHERE funcionario_id = ? AND id <> ?", (funcionario_id, usuario_id))
        if cur.fetchone():
            raise ValueError('Este funcionário já possui outro usuário.')
    cur.execute("UPDATE usuarios SET funcionario_id = ?, nivel_acesso = ?, ativo = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (funcionario_id or None, _normalize_perfil_acesso(nivel_acesso) or 'VISUALIZACAO', 1 if ativo else 0, usuario_id))
    conn.commit()


def resetar_senha_usuario(usuario_id: str, enviar_email: bool = True):
    cur = _cursor()
    cur.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Usuário não encontrado.')
    row = dict(row)
    senha_temporaria = _gerar_senha_temporaria() if '_gerar_senha_temporaria' in globals() else 'Temp12345'
    alterar_senha_usuario(usuario_id, senha_temporaria, True)
    email_enviado = False
    if enviar_email and 'enviar_credenciais_usuario_email' in globals():
        email = _normalizar_email(row.get('email')) if '_normalizar_email' in globals() else str(row.get('email') or '').strip()
        if not email:
            raise ValueError('Usuário sem e-mail cadastrado para envio de credenciais.')
        try:
            email_enviado = enviar_credenciais_usuario_email(row.get('nome') or row.get('username'), email, row.get('username'), senha_temporaria)
        except Exception:
            email_enviado = False
    return {'id': usuario_id, 'username': row.get('username'), 'senha_temporaria': senha_temporaria, 'email_enviado': email_enviado}


def excluir_usuario(usuario_id: str):
    cur = _cursor()
    cur.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    conn.commit()


def get_escala_para_data(funcionario_id: str, data_iso: str):
    func = get_funcionario(funcionario_id)
    if not func or not func.get('escala_id'):
        return None
    cur = _cursor()
    cur.execute("SELECT * FROM escalas WHERE id = ?", (func['escala_id'],))
    esc_row = cur.fetchone()
    if not esc_row:
        return None
    esc = dict(esc_row)
    import datetime as _dt
    try:
        dt = _dt.datetime.strptime(str(data_iso), '%Y-%m-%d')
    except Exception:
        return None
    mapa = ['seg', 'ter', 'qua', 'qui', 'sex', 'sab', 'dom']
    chave = mapa[dt.weekday()]
    inicio = esc.get(f'{chave}_inicio')
    fim = esc.get(f'{chave}_fim')
    if not inicio or not fim:
        return None
    return {
        'inicio': inicio,
        'fim': fim,
        'intervalo_inicio': esc.get(f'{chave}_int_inicio'),
        'intervalo_fim': esc.get(f'{chave}_int_fim'),
        'nome': esc.get('nome'),
    }


def _hora_to_min(hhmm: str) -> int:
    try:
        h, m = str(hhmm).split(':')
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def validar_conflito_apontamento(funcionario_id: str, data_apontamento: str, hora_inicio: str, hora_fim: str, ignorar_apontamento_id: str = None):
    if not funcionario_id:
        return
    ini = _hora_to_min(hora_inicio)
    fim = _hora_to_min(hora_fim)
    if fim <= ini:
        fim += 24 * 60
    cur = _cursor()
    if ignorar_apontamento_id:
        cur.execute("SELECT * FROM os_apontamentos WHERE funcionario_id = ? AND data_apontamento = ? AND id <> ?", (funcionario_id, data_apontamento, ignorar_apontamento_id))
    else:
        cur.execute("SELECT * FROM os_apontamentos WHERE funcionario_id = ? AND data_apontamento = ?", (funcionario_id, data_apontamento))
    for row in cur.fetchall():
        rini = _hora_to_min(row['hora_inicio'])
        rfim = _hora_to_min(row['hora_fim'])
        if rfim <= rini:
            rfim += 24 * 60
        if ini < rfim and fim > rini:
            raise ValueError('Este funcionário já possui apontamento em outra OS neste dia e horário.')

# sobrescreve criação de apontamento para usar funcionário/escala/conflito
_old_criar_os_apontamento = criar_os_apontamento

def criar_os_apontamento(
    os_id: str,
    atividade_id: str,
    funcionario_id: str = None,
    funcionario_nome: str = None,
    equipe_nome: Optional[str] = None,
    hora_inicio: Optional[str] = None,
    hora_fim: Optional[str] = None,
    duracao_min: Optional[float] = None,
    descricao_servico: Optional[str] = None,
    observacao: Optional[str] = None,
    data_apontamento: Optional[str] = None,
    usar_escala: bool = False,
):
    if funcionario_id:
        func = get_funcionario(funcionario_id)
        if not func:
            raise ValueError('Funcionário não encontrado.')
        funcionario_nome = func['nome']
        equipe_nome = func.get('equipe_nome') or equipe_nome
        if usar_escala:
            escala = get_escala_para_data(funcionario_id, data_apontamento)
            if not escala:
                raise ValueError('Não existe jornada definida na escala para este dia.')
            hora_inicio = escala['inicio']
            hora_fim = escala['fim']
            duracao_min = _hora_to_min(hora_fim) - _hora_to_min(hora_inicio)
            if duracao_min < 0:
                duracao_min += 24 * 60
            int_ini = escala.get('intervalo_inicio')
            int_fim = escala.get('intervalo_fim')
            if int_ini and int_fim:
                duracao_min -= max(0, _hora_to_min(int_fim) - _hora_to_min(int_ini))
            duracao_min = max(0, duracao_min)
        if duracao_min and not hora_fim and hora_inicio:
            total = _hora_to_min(hora_inicio) + int(float(duracao_min))
            hora_fim = f"{(total // 60) % 24:02d}:{total % 60:02d}"
        if funcionario_id and data_apontamento and hora_inicio and hora_fim:
            validar_conflito_apontamento(funcionario_id, data_apontamento, hora_inicio, hora_fim)
    ap_id = _old_criar_os_apontamento(
        os_id=os_id,
        atividade_id=atividade_id,
        funcionario_nome=funcionario_nome,
        equipe_nome=equipe_nome,
        hora_inicio=hora_inicio,
        hora_fim=hora_fim,
        duracao_min=duracao_min,
        descricao_servico=descricao_servico,
        observacao=observacao,
        data_apontamento=data_apontamento,
    )
    cur = _cursor()
    cur.execute("UPDATE os_apontamentos SET funcionario_id = ?, equipe_id = ?, modo_hora = ? WHERE id = ?", (funcionario_id, (func.get('equipe_id') if funcionario_id and func else None), 'ESCALA' if usar_escala else 'MANUAL', ap_id))
    conn.commit()
    return ap_id


# =========================
# PATCH STAGE 2 - OS/PEOPLE/UI RULES
# =========================

def _ensure_stage2_schema():
    cur = _cursor()
    try:
        cols = _get_columns('ordens_servico')
        add = {
            'custo_terceiro': "ALTER TABLE ordens_servico ADD COLUMN custo_terceiro REAL NULL",
            'descricao_servico_terceiro': "ALTER TABLE ordens_servico ADD COLUMN descricao_servico_terceiro TEXT NULL",
            'usuario_encerramento': "ALTER TABLE ordens_servico ADD COLUMN usuario_encerramento TEXT NULL",
            'usuario_encerramento_id': "ALTER TABLE ordens_servico ADD COLUMN usuario_encerramento_id TEXT NULL",
        }
        for c, ddl in add.items():
            if c not in cols:
                cur.execute(ddl)
    except Exception:
        pass
    try:
        cols = _get_columns('os_apontamentos')
        add = {
            'custo_hh_unitario': "ALTER TABLE os_apontamentos ADD COLUMN custo_hh_unitario REAL NULL",
            'custo_hh_total': "ALTER TABLE os_apontamentos ADD COLUMN custo_hh_total REAL NULL",
        }
        for c, ddl in add.items():
            if c not in cols:
                cur.execute(ddl)
    except Exception:
        pass
    try:
        cols = _get_columns('escalas')
        add = {
            'seg_int_inicio': "ALTER TABLE escalas ADD COLUMN seg_int_inicio TEXT NULL",
            'seg_int_fim': "ALTER TABLE escalas ADD COLUMN seg_int_fim TEXT NULL",
            'ter_int_inicio': "ALTER TABLE escalas ADD COLUMN ter_int_inicio TEXT NULL",
            'ter_int_fim': "ALTER TABLE escalas ADD COLUMN ter_int_fim TEXT NULL",
            'qua_int_inicio': "ALTER TABLE escalas ADD COLUMN qua_int_inicio TEXT NULL",
            'qua_int_fim': "ALTER TABLE escalas ADD COLUMN qua_int_fim TEXT NULL",
            'qui_int_inicio': "ALTER TABLE escalas ADD COLUMN qui_int_inicio TEXT NULL",
            'qui_int_fim': "ALTER TABLE escalas ADD COLUMN qui_int_fim TEXT NULL",
            'sex_int_inicio': "ALTER TABLE escalas ADD COLUMN sex_int_inicio TEXT NULL",
            'sex_int_fim': "ALTER TABLE escalas ADD COLUMN sex_int_fim TEXT NULL",
            'sab_int_inicio': "ALTER TABLE escalas ADD COLUMN sab_int_inicio TEXT NULL",
            'sab_int_fim': "ALTER TABLE escalas ADD COLUMN sab_int_fim TEXT NULL",
            'dom_int_inicio': "ALTER TABLE escalas ADD COLUMN dom_int_inicio TEXT NULL",
            'dom_int_fim': "ALTER TABLE escalas ADD COLUMN dom_int_fim TEXT NULL",
        }
        for c, ddl in add.items():
            if c not in cols:
                cur.execute(ddl)
    except Exception:
        pass
    cur.execute("""
        CREATE TABLE IF NOT EXISTS os_anexos (
            id TEXT PRIMARY KEY,
            os_id TEXT NOT NULL,
            nome_original TEXT NOT NULL,
            nome_salvo TEXT NOT NULL,
            caminho TEXT NOT NULL,
            tipo TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(os_id) REFERENCES ordens_servico(id) ON DELETE CASCADE
        )
    """)
    try:
        cols = _get_columns('os_anexos')
        add = {
            'bucket': "ALTER TABLE os_anexos ADD COLUMN bucket TEXT NULL",
            'storage_path': "ALTER TABLE os_anexos ADD COLUMN storage_path TEXT NULL",
            'url_publica': "ALTER TABLE os_anexos ADD COLUMN url_publica TEXT NULL",
        }
        for c, ddl in add.items():
            if c not in cols:
                cur.execute(ddl)
    except Exception:
        pass
    conn.commit()
    # garante admin inicial correto
    cur.execute("SELECT id, password FROM usuarios WHERE lower(username) = 'admin' LIMIT 1")
    row = cur.fetchone()
    if row:
        if str(row['password'] or '') == '1234':
            cur.execute("UPDATE usuarios SET password = 'funsolos1980', nivel_acesso = 'COMPLETO', ativo = 1, deve_trocar_senha = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (row['id'],))
    else:
        cur.execute("INSERT INTO usuarios (id, funcionario_id, username, password, nivel_acesso, ativo, deve_trocar_senha, created_at, updated_at) VALUES (?, NULL, 'admin', 'funsolos1980', 'COMPLETO', 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (str(uuid.uuid4()),))
    conn.commit()

_ensure_stage2_schema()



def listar_os_anexos(os_id: str):
    cur = _cursor()
    cur.execute("SELECT * FROM os_anexos WHERE os_id = ? ORDER BY created_at DESC, nome_original", (os_id,))
    itens = []
    for row in cur.fetchall():
        item = _migrar_linha_anexo_para_supabase('os_anexos', dict(row), 'os_id', SUPABASE_STORAGE_FOLDER_OS)
        itens.append(_hydrate_storage_fields(item))
    return itens

def adicionar_os_anexo(os_id: str, origem_path: str, nome_original: str):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada para anexar arquivo.')
    extensao = Path(nome_original).suffix.lower()
    if extensao in ['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif']:
        tipo = 'FOTO'
    elif extensao == '.pdf':
        tipo = 'PDF'
    else:
        raise ValueError('Tipo de arquivo inválido. Envie apenas imagens ou PDF.')
    anexo_id = str(uuid.uuid4())
    pasta_os = UPLOAD_ATIVOS_DIR / 'os' / os_id
    pasta_os.mkdir(parents=True, exist_ok=True)
    nome_salvo = f'{anexo_id}{extensao}'
    destino = pasta_os / nome_salvo
    shutil.copy2(origem_path, destino)

    bucket = None
    storage_path = None
    url_publica = None
    if SUPABASE_UPLOADS_ENABLED and _supabase_ready():
        storage_path = f"{SUPABASE_STORAGE_FOLDER_OS}/{os_id}/{nome_salvo}"
        payload = Path(origem_path).read_bytes()
        _supabase_upload_bytes(SUPABASE_BUCKET_UPLOADS, storage_path, payload, _guess_content_type(nome_original, tipo))
        bucket = SUPABASE_BUCKET_UPLOADS
        url_publica = f'{SUPABASE_URL}/storage/v1/object/public/{bucket}/{storage_path}'

    cur = _cursor()
    cur.execute("INSERT INTO os_anexos (id, os_id, nome_original, nome_salvo, caminho, tipo, bucket, storage_path, url_publica, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)", (anexo_id, os_id, nome_original, nome_salvo, str(destino), tipo, bucket, storage_path, url_publica))
    conn.commit()
    return _hydrate_storage_fields({'id': anexo_id, 'os_id': os_id, 'nome_original': nome_original, 'nome_salvo': nome_salvo, 'caminho': str(destino), 'tipo': tipo, 'bucket': bucket, 'storage_path': storage_path, 'url_publica': url_publica})

def remover_os_anexo(anexo_id: str):
    cur = _cursor()
    cur.execute("SELECT * FROM os_anexos WHERE id = ?", (anexo_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Anexo da OS não encontrado.')
    item = dict(row)
    caminho = item.get('caminho')
    bucket = item.get('bucket')
    storage_path = item.get('storage_path')
    cur.execute("DELETE FROM os_anexos WHERE id = ?", (anexo_id,))
    conn.commit()
    if bucket and storage_path:
        _supabase_delete_object(bucket, storage_path)
    if caminho and os.path.exists(caminho):
        try:
            os.remove(caminho)
        except Exception:
            pass

def criar_escala(nome: str, dias: dict, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da escala.')
    esc_id = str(uuid.uuid4())
    payload = {k: _text(v) or None for k, v in (dias or {}).items()}
    cur = _cursor()
    cur.execute("""
        INSERT INTO escalas (
            id, nome, ativo,
            seg_inicio, seg_fim, seg_int_inicio, seg_int_fim,
            ter_inicio, ter_fim, ter_int_inicio, ter_int_fim,
            qua_inicio, qua_fim, qua_int_inicio, qua_int_fim,
            qui_inicio, qui_fim, qui_int_inicio, qui_int_fim,
            sex_inicio, sex_fim, sex_int_inicio, sex_int_fim,
            sab_inicio, sab_fim, sab_int_inicio, sab_int_fim,
            dom_inicio, dom_fim, dom_int_inicio, dom_int_fim,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        esc_id, nome, 1 if ativo else 0,
        payload.get('seg_inicio'), payload.get('seg_fim'), payload.get('seg_int_inicio'), payload.get('seg_int_fim'),
        payload.get('ter_inicio'), payload.get('ter_fim'), payload.get('ter_int_inicio'), payload.get('ter_int_fim'),
        payload.get('qua_inicio'), payload.get('qua_fim'), payload.get('qua_int_inicio'), payload.get('qua_int_fim'),
        payload.get('qui_inicio'), payload.get('qui_fim'), payload.get('qui_int_inicio'), payload.get('qui_int_fim'),
        payload.get('sex_inicio'), payload.get('sex_fim'), payload.get('sex_int_inicio'), payload.get('sex_int_fim'),
        payload.get('sab_inicio'), payload.get('sab_fim'), payload.get('sab_int_inicio'), payload.get('sab_int_fim'),
        payload.get('dom_inicio'), payload.get('dom_fim'), payload.get('dom_int_inicio'), payload.get('dom_int_fim'),
    ))
    conn.commit()
    return esc_id


def atualizar_escala(escala_id: str, nome: str, dias: dict, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da escala.')
    payload = {k: _text(v) or None for k, v in (dias or {}).items()}
    cur = _cursor()
    cur.execute("""
        UPDATE escalas
        SET nome = ?, ativo = ?,
            seg_inicio = ?, seg_fim = ?, seg_int_inicio = ?, seg_int_fim = ?,
            ter_inicio = ?, ter_fim = ?, ter_int_inicio = ?, ter_int_fim = ?,
            qua_inicio = ?, qua_fim = ?, qua_int_inicio = ?, qua_int_fim = ?,
            qui_inicio = ?, qui_fim = ?, qui_int_inicio = ?, qui_int_fim = ?,
            sex_inicio = ?, sex_fim = ?, sex_int_inicio = ?, sex_int_fim = ?,
            sab_inicio = ?, sab_fim = ?, sab_int_inicio = ?, sab_int_fim = ?,
            dom_inicio = ?, dom_fim = ?, dom_int_inicio = ?, dom_int_fim = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        nome, 1 if ativo else 0,
        payload.get('seg_inicio'), payload.get('seg_fim'), payload.get('seg_int_inicio'), payload.get('seg_int_fim'),
        payload.get('ter_inicio'), payload.get('ter_fim'), payload.get('ter_int_inicio'), payload.get('ter_int_fim'),
        payload.get('qua_inicio'), payload.get('qua_fim'), payload.get('qua_int_inicio'), payload.get('qua_int_fim'),
        payload.get('qui_inicio'), payload.get('qui_fim'), payload.get('qui_int_inicio'), payload.get('qui_int_fim'),
        payload.get('sex_inicio'), payload.get('sex_fim'), payload.get('sex_int_inicio'), payload.get('sex_int_fim'),
        payload.get('sab_inicio'), payload.get('sab_fim'), payload.get('sab_int_inicio'), payload.get('sab_int_fim'),
        payload.get('dom_inicio'), payload.get('dom_fim'), payload.get('dom_int_inicio'), payload.get('dom_int_fim'),
        escala_id,
    ))
    conn.commit()


def get_escala_para_data(funcionario_id: str, data_iso: str):
    func = get_funcionario(funcionario_id)
    if not func or not func.get('escala_id'):
        return None
    cur = _cursor()
    cur.execute("SELECT * FROM escalas WHERE id = ?", (func['escala_id'],))
    esc = cur.fetchone()
    if not esc:
        return None
    import datetime as _dt
    try:
        dt = _dt.datetime.strptime(str(data_iso), '%Y-%m-%d')
    except Exception:
        return None
    mapa = ['seg', 'ter', 'qua', 'qui', 'sex', 'sab', 'dom']
    chave = mapa[dt.weekday()]
    inicio = esc[f'{chave}_inicio']
    fim = esc[f'{chave}_fim']
    int_inicio = esc.get(f'{chave}_int_inicio') if hasattr(esc, 'keys') else esc[f'{chave}_int_inicio']
    int_fim = esc.get(f'{chave}_int_fim') if hasattr(esc, 'keys') else esc[f'{chave}_int_fim']
    if not inicio or not fim:
        return None
    return {'inicio': inicio, 'fim': fim, 'intervalo_inicio': int_inicio, 'intervalo_fim': int_fim, 'nome': esc['nome']}


def proximo_numero_os() -> str:
    cur = _cursor()
    cur.execute("SELECT strftime('%Y', 'now')")
    ano = str(cur.fetchone()[0])
    cur.execute("SELECT numero FROM ordens_servico WHERE numero LIKE ? ORDER BY numero DESC LIMIT 1", (f'OS-{ano}-%',))
    row = cur.fetchone()
    sequencia = 1
    if row and row['numero']:
        try:
            sequencia = int(str(row['numero']).split('-')[-1]) + 1
        except Exception:
            sequencia = 1
    return f'OS-{ano}-{sequencia:04d}'


def criar_os(alvo_ativo_id: str, descricao: str, tipo_os: Optional[str] = None, prioridade: Optional[str] = None,
             observacoes: Optional[str] = None, numero: Optional[str] = None, data_abertura: Optional[str] = None,
             unidade_medidor: Optional[str] = None, medidor_valor: Optional[float] = None, status: Optional[str] = None,
             justificativa_encerramento: Optional[str] = None, custo_terceiro: Optional[float] = None,
             descricao_servico_terceiro: Optional[str] = None):
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da OS.')
    alvo = _resolver_alvo_os(alvo_ativo_id)
    os_id = str(uuid.uuid4())
    numero = _upper(numero) or proximo_numero_os()
    novo_status = _upper(status) or 'ABERTA'
    if novo_status == 'ENCERRADA':
        validar_encerramento_os(os_id, justificativa_encerramento)
    cur = _cursor()
    cur.execute("""
        INSERT INTO ordens_servico (
            id, numero, origem_tipo, equipamento_id, componente_id,
            status, prioridade, tipo_os, descricao, observacoes,
            justificativa_encerramento, data_abertura, data_encerramento,
            unidade_medidor, medidor_valor, custo_terceiro, descricao_servico_terceiro,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        os_id, numero, alvo['origem_tipo'], alvo['equipamento_id'], alvo['componente_id'],
        novo_status, _upper(prioridade), _upper(tipo_os), descricao, _text(observacoes),
        _text(justificativa_encerramento), _text(data_abertura) or _agora_sql()[:10], _agora_sql() if novo_status == 'ENCERRADA' else None,
        _upper(unidade_medidor) or 'HORÍMETRO', float(medidor_valor) if medidor_valor not in ('', None) else None,
        float(custo_terceiro) if custo_terceiro not in ('', None) else 0.0, _text(descricao_servico_terceiro),
    ))
    conn.commit()
    return get_os(os_id)


def atualizar_os(os_id: str, alvo_ativo_id: str, descricao: str, tipo_os: Optional[str] = None, prioridade: Optional[str] = None,
                 observacoes: Optional[str] = None, status: Optional[str] = None, justificativa_encerramento: Optional[str] = None,
                 data_abertura: Optional[str] = None, unidade_medidor: Optional[str] = None, medidor_valor: Optional[float] = None,
                 custo_terceiro: Optional[float] = None, descricao_servico_terceiro: Optional[str] = None):
    atual = _get_os_row(os_id)
    if not atual:
        raise ValueError('OS não encontrada.')
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da OS.')
    alvo = _resolver_alvo_os(alvo_ativo_id)
    novo_status = _upper(status or atual['status'] or 'ABERTA')
    justificativa_encerramento = _text(justificativa_encerramento)
    if novo_status == 'ENCERRADA':
        validar_encerramento_os(os_id, justificativa_encerramento)
    data_encerramento = _agora_sql() if novo_status == 'ENCERRADA' else None
    cur = _cursor()
    cur.execute("""
        UPDATE ordens_servico
        SET origem_tipo = ?, equipamento_id = ?, componente_id = ?, descricao = ?, tipo_os = ?, prioridade = ?,
            observacoes = ?, data_abertura = ?, unidade_medidor = ?, medidor_valor = ?, status = ?,
            justificativa_encerramento = ?, data_encerramento = ?, custo_terceiro = ?, descricao_servico_terceiro = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        alvo['origem_tipo'], alvo['equipamento_id'], alvo['componente_id'], descricao, _upper(tipo_os), _upper(prioridade),
        _text(observacoes), _text(data_abertura) or _text(atual['data_abertura']), _upper(unidade_medidor) or _upper(atual['unidade_medidor']) or 'HORÍMETRO',
        float(medidor_valor) if medidor_valor not in ('', None) else atual['medidor_valor'], novo_status,
        justificativa_encerramento, data_encerramento,
        float(custo_terceiro) if custo_terceiro not in ('', None) else float(atual['custo_terceiro'] or 0), _text(descricao_servico_terceiro),
        os_id,
    ))
    conn.commit()
    return get_os(os_id)


def get_os(os_id: str):
    row = _get_os_row(os_id)
    if not row:
        return None
    item = dict(row)
    equipamento = get_ativo(item['equipamento_id']) if item.get('equipamento_id') else None
    componente = get_ativo(item['componente_id']) if item.get('componente_id') else None
    item['equipamento'] = equipamento
    item['componente'] = componente
    item['total_atividades'] = len(listar_os_atividades(os_id))
    item['total_materiais'] = len(listar_os_materiais(os_id))
    item['total_apontamentos'] = len(listar_os_apontamentos(os_id))
    return item


def calcular_totais_os(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT
            COALESCE((SELECT SUM(custo_total) FROM os_materiais WHERE os_id = ?), 0) AS custo_materiais,
            COALESCE((SELECT SUM(duracao_min) FROM os_apontamentos WHERE os_id = ?), 0) AS hh_min,
            COALESCE((SELECT SUM(COALESCE(ap.custo_hh_total, ROUND((ap.duracao_min / 60.0) * COALESCE(f.custo_hh, 0), 2), 0)) FROM os_apontamentos ap LEFT JOIN funcionarios f ON f.id = ap.funcionario_id WHERE ap.os_id = ?), 0) AS custo_hh,
            COALESCE((SELECT custo_terceiro FROM ordens_servico WHERE id = ?), 0) AS custo_terceiro
    """, (os_id, os_id, os_id, os_id))
    row = cur.fetchone()
    custo_materiais = float(row['custo_materiais'] or 0)
    hh_min = float(row['hh_min'] or 0)
    custo_hh = float(row['custo_hh'] or 0)
    custo_terceiro = float(row['custo_terceiro'] or 0)
    return {
        'custo_materiais': custo_materiais,
        'hh_min': hh_min,
        'hh_horas': round(hh_min / 60, 2),
        'custo_hh': custo_hh,
        'custo_terceiro': custo_terceiro,
        'custo_total_os': round(custo_materiais + custo_hh + custo_terceiro, 2),
    }


def criar_os_apontamento(os_id: str, atividade_id: str, funcionario_id: str = None, funcionario_nome: str = None,
                         equipe_nome: Optional[str] = None, hora_inicio: Optional[str] = None, hora_fim: Optional[str] = None,
                         duracao_min: Optional[float] = None, descricao_servico: Optional[str] = None, observacao: Optional[str] = None,
                         data_apontamento: Optional[str] = None, usar_escala: bool = False):
    func = None
    custo_hh_unitario = 0.0
    if funcionario_id:
        func = get_funcionario(funcionario_id)
        if not func:
            raise ValueError('Funcionário não encontrado.')
        funcionario_nome = func['nome']
        equipe_nome = func.get('equipe_nome') or equipe_nome
        custo_hh_unitario = float(func.get('custo_hh') or 0)
        if usar_escala:
            escala = get_escala_para_data(funcionario_id, data_apontamento)
            if not escala:
                raise ValueError('Não existe jornada definida na escala para este dia.')
            hora_inicio = escala['inicio']
            hora_fim = escala['fim']
            duracao_min = _hora_to_min(hora_fim) - _hora_to_min(hora_inicio)
            if escala.get('intervalo_inicio') and escala.get('intervalo_fim'):
                duracao_min -= max(0, _hora_to_min(escala['intervalo_fim']) - _hora_to_min(escala['intervalo_inicio']))
        if duracao_min and not hora_fim and hora_inicio:
            total = _hora_to_min(hora_inicio) + int(float(duracao_min))
            hora_fim = f"{(total // 60) % 24:02d}:{total % 60:02d}"
        validar_conflito_apontamento(funcionario_id, data_apontamento, hora_inicio, hora_fim)
    ap_id = _old_criar_os_apontamento(
        os_id=os_id, atividade_id=atividade_id, funcionario_nome=funcionario_nome, equipe_nome=equipe_nome,
        hora_inicio=hora_inicio, hora_fim=hora_fim, duracao_min=duracao_min, descricao_servico=descricao_servico,
        observacao=observacao, data_apontamento=data_apontamento,
    )
    custo_total = round((float(duracao_min or 0) / 60.0) * custo_hh_unitario, 2)
    cur = _cursor()
    cur.execute("UPDATE os_apontamentos SET funcionario_id = ?, equipe_id = ?, modo_hora = ?, custo_hh_unitario = ?, custo_hh_total = ? WHERE id = ?",
                (funcionario_id, (func.get('equipe_id') if func else None), 'ESCALA' if usar_escala else 'MANUAL', custo_hh_unitario, custo_total, ap_id))
    conn.commit()
    return ap_id


# =========================
# PATCH STAGE 3 - ESCALAS COM INTERVALO + ATIVIDADES OPERACIONAIS
# =========================

def _ensure_stage3_schema():
    cur = _cursor()
    try:
        cols = _get_columns('os_atividades')
        add = {
            'classificacao': "ALTER TABLE os_atividades ADD COLUMN classificacao TEXT NULL",
            'duracao_min': "ALTER TABLE os_atividades ADD COLUMN duracao_min REAL NULL",
            'custo_hh': "ALTER TABLE os_atividades ADD COLUMN custo_hh REAL NULL",
            'custo_servico_terceiro': "ALTER TABLE os_atividades ADD COLUMN custo_servico_terceiro REAL NULL",
        }
        for c, ddl in add.items():
            if c not in cols:
                cur.execute(ddl)
    except Exception:
        pass
    conn.commit()

_ensure_stage3_schema()


def criar_escala(nome: str, dias: dict, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da escala.')
    esc_id = str(uuid.uuid4())
    payload = {k: _text(v) or None for k, v in (dias or {}).items()}
    cur = _cursor()
    cur.execute("""
        INSERT INTO escalas (
            id, nome, ativo,
            seg_inicio, seg_fim, seg_int_inicio, seg_int_fim,
            ter_inicio, ter_fim, ter_int_inicio, ter_int_fim,
            qua_inicio, qua_fim, qua_int_inicio, qua_int_fim,
            qui_inicio, qui_fim, qui_int_inicio, qui_int_fim,
            sex_inicio, sex_fim, sex_int_inicio, sex_int_fim,
            sab_inicio, sab_fim, sab_int_inicio, sab_int_fim,
            dom_inicio, dom_fim, dom_int_inicio, dom_int_fim,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
    """, (
        esc_id, nome, 1 if ativo else 0,
        payload.get('seg_inicio'), payload.get('seg_fim'), payload.get('seg_int_inicio'), payload.get('seg_int_fim'),
        payload.get('ter_inicio'), payload.get('ter_fim'), payload.get('ter_int_inicio'), payload.get('ter_int_fim'),
        payload.get('qua_inicio'), payload.get('qua_fim'), payload.get('qua_int_inicio'), payload.get('qua_int_fim'),
        payload.get('qui_inicio'), payload.get('qui_fim'), payload.get('qui_int_inicio'), payload.get('qui_int_fim'),
        payload.get('sex_inicio'), payload.get('sex_fim'), payload.get('sex_int_inicio'), payload.get('sex_int_fim'),
        payload.get('sab_inicio'), payload.get('sab_fim'), payload.get('sab_int_inicio'), payload.get('sab_int_fim'),
        payload.get('dom_inicio'), payload.get('dom_fim'), payload.get('dom_int_inicio'), payload.get('dom_int_fim'),
    ))
    conn.commit()
    return esc_id


def atualizar_escala(escala_id: str, nome: str, dias: dict, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da escala.')
    payload = {k: _text(v) or None for k, v in (dias or {}).items()}
    cur = _cursor()
    cur.execute("""
        UPDATE escalas
        SET nome = ?, ativo = ?,
            seg_inicio = ?, seg_fim = ?, seg_int_inicio = ?, seg_int_fim = ?,
            ter_inicio = ?, ter_fim = ?, ter_int_inicio = ?, ter_int_fim = ?,
            qua_inicio = ?, qua_fim = ?, qua_int_inicio = ?, qua_int_fim = ?,
            qui_inicio = ?, qui_fim = ?, qui_int_inicio = ?, qui_int_fim = ?,
            sex_inicio = ?, sex_fim = ?, sex_int_inicio = ?, sex_int_fim = ?,
            sab_inicio = ?, sab_fim = ?, sab_int_inicio = ?, sab_int_fim = ?,
            dom_inicio = ?, dom_fim = ?, dom_int_inicio = ?, dom_int_fim = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        nome, 1 if ativo else 0,
        payload.get('seg_inicio'), payload.get('seg_fim'), payload.get('seg_int_inicio'), payload.get('seg_int_fim'),
        payload.get('ter_inicio'), payload.get('ter_fim'), payload.get('ter_int_inicio'), payload.get('ter_int_fim'),
        payload.get('qua_inicio'), payload.get('qua_fim'), payload.get('qua_int_inicio'), payload.get('qua_int_fim'),
        payload.get('qui_inicio'), payload.get('qui_fim'), payload.get('qui_int_inicio'), payload.get('qui_int_fim'),
        payload.get('sex_inicio'), payload.get('sex_fim'), payload.get('sex_int_inicio'), payload.get('sex_int_fim'),
        payload.get('sab_inicio'), payload.get('sab_fim'), payload.get('sab_int_inicio'), payload.get('sab_int_fim'),
        payload.get('dom_inicio'), payload.get('dom_fim'), payload.get('dom_int_inicio'), payload.get('dom_int_fim'),
        escala_id,
    ))
    conn.commit()


def listar_os_atividades(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT a.*,
               (SELECT COUNT(*) FROM os_materiais m WHERE m.atividade_id = a.id) AS total_materiais,
               COALESCE((SELECT SUM(custo_total) FROM os_materiais m WHERE m.atividade_id = a.id), 0) AS custo_materiais
        FROM os_atividades a
        WHERE a.os_id = ?
        ORDER BY a.sequencia, a.created_at
    """, (os_id,))
    return [dict(r) for r in cur.fetchall()]


def criar_os_atividade(os_id: str, descricao: str, observacao: Optional[str] = None,
                       status: Optional[str] = None, classificacao: Optional[str] = None,
                       duracao_min: Optional[float] = None, custo_hh: Optional[float] = None,
                       custo_servico_terceiro: Optional[float] = None):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da atividade.')
    cur = _cursor()
    cur.execute("SELECT COALESCE(MAX(sequencia), 0) + 1 FROM os_atividades WHERE os_id = ?", (os_id,))
    sequencia = int(cur.fetchone()[0] or 1)
    atividade_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO os_atividades (
            id, os_id, sequencia, descricao, status, observacao, classificacao,
            duracao_min, custo_hh, custo_servico_terceiro, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        atividade_id, os_id, sequencia, descricao, _upper(status) or 'ABERTA', _text(observacao),
        _upper(classificacao) or 'INTERNA', float(duracao_min or 0), float(custo_hh or 0), float(custo_servico_terceiro or 0),
    ))
    conn.commit()
    return dict(_get_atividade_row(atividade_id))


def atualizar_os_atividade(atividade_id: str, descricao: str, observacao: Optional[str] = None,
                           status: Optional[str] = None, classificacao: Optional[str] = None,
                           duracao_min: Optional[float] = None, custo_hh: Optional[float] = None,
                           custo_servico_terceiro: Optional[float] = None):
    atual = _get_atividade_row(atividade_id)
    if not atual:
        raise ValueError('Atividade não encontrada.')
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da atividade.')
    novo_status = _upper(status or atual['status'] or 'ABERTA')
    cur = _cursor()
    cur.execute("""
        UPDATE os_atividades
        SET descricao = ?, observacao = ?, status = ?, classificacao = ?,
            duracao_min = ?, custo_hh = ?, custo_servico_terceiro = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        descricao, _text(observacao), novo_status, _upper(classificacao) or 'INTERNA',
        float(duracao_min or 0), float(custo_hh or 0), float(custo_servico_terceiro or 0), atividade_id,
    ))
    conn.commit()
    return dict(_get_atividade_row(atividade_id))


def calcular_totais_os(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT
            COALESCE((SELECT SUM(custo_total) FROM os_materiais WHERE os_id = ?), 0) AS custo_materiais,
            COALESCE((SELECT SUM(duracao_min) FROM os_atividades WHERE os_id = ?), 0) AS hh_min,
            COALESCE((SELECT SUM(custo_hh) FROM os_atividades WHERE os_id = ?), 0) AS custo_hh,
            COALESCE((SELECT SUM(custo_servico_terceiro) FROM os_atividades WHERE os_id = ?), 0) AS custo_terceiro
    """, (os_id, os_id, os_id, os_id))
    row = cur.fetchone()
    custo_materiais = float(row['custo_materiais'] or 0)
    hh_min = float(row['hh_min'] or 0)
    custo_hh = float(row['custo_hh'] or 0)
    custo_terceiro = float(row['custo_terceiro'] or 0)
    return {
        'custo_materiais': custo_materiais,
        'hh_min': hh_min,
        'hh_horas': round(hh_min / 60, 2),
        'custo_hh': custo_hh,
        'custo_terceiro': custo_terceiro,
        'custo_total_os': round(custo_materiais + custo_hh + custo_terceiro, 2),
    }


# =========================
# PATCH STAGE 4 - ESCALA FIX + APONTAMENTO INTERNO / TERCEIRO
# =========================

def criar_escala(nome: str, dias: dict, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da escala.')
    esc_id = str(uuid.uuid4())
    payload = {k: _text(v) or None for k, v in (dias or {}).items()}
    campos = [
        'seg_inicio','seg_int_inicio','seg_int_fim','seg_fim',
        'ter_inicio','ter_int_inicio','ter_int_fim','ter_fim',
        'qua_inicio','qua_int_inicio','qua_int_fim','qua_fim',
        'qui_inicio','qui_int_inicio','qui_int_fim','qui_fim',
        'sex_inicio','sex_int_inicio','sex_int_fim','sex_fim',
        'sab_inicio','sab_int_inicio','sab_int_fim','sab_fim',
        'dom_inicio','dom_int_inicio','dom_int_fim','dom_fim',
    ]
    valores = [esc_id, nome, 1 if ativo else 0] + [payload.get(c) for c in campos]
    placeholders = ', '.join(['?'] * len(valores))
    sql = f"""
        INSERT INTO escalas (
            id, nome, ativo,
            {', '.join(campos)},
            created_at, updated_at
        ) VALUES (
            {placeholders},
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
    """
    cur = _cursor()
    cur.execute(sql, tuple(valores))
    conn.commit()
    return esc_id


def atualizar_escala(escala_id: str, nome: str, dias: dict, ativo: bool = True):
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome da escala.')
    payload = {k: _text(v) or None for k, v in (dias or {}).items()}
    campos = [
        'seg_inicio','seg_int_inicio','seg_int_fim','seg_fim',
        'ter_inicio','ter_int_inicio','ter_int_fim','ter_fim',
        'qua_inicio','qua_int_inicio','qua_int_fim','qua_fim',
        'qui_inicio','qui_int_inicio','qui_int_fim','qui_fim',
        'sex_inicio','sex_int_inicio','sex_int_fim','sex_fim',
        'sab_inicio','sab_int_inicio','sab_int_fim','sab_fim',
        'dom_inicio','dom_int_inicio','dom_int_fim','dom_fim',
    ]
    set_sql = ', '.join([f'{c} = ?' for c in campos])
    valores = [nome, 1 if ativo else 0] + [payload.get(c) for c in campos] + [escala_id]
    sql = f"""
        UPDATE escalas
        SET nome = ?, ativo = ?,
            {set_sql},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """
    cur = _cursor()
    cur.execute(sql, tuple(valores))
    conn.commit()


def listar_os_atividades(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT
            a.*,
            COALESCE((SELECT COUNT(*) FROM os_apontamentos ap WHERE ap.atividade_id = a.id), 0) AS total_apontamentos,
            COALESCE((SELECT SUM(ap.duracao_min) FROM os_apontamentos ap WHERE ap.atividade_id = a.id), 0) AS duracao_apontada_min,
            COALESCE((SELECT SUM(ap.custo_hh_total) FROM os_apontamentos ap WHERE ap.atividade_id = a.id), 0) AS custo_hh_apontado,
            COALESCE((SELECT COUNT(*) FROM os_materiais m WHERE m.atividade_id = a.id), 0) AS total_materiais,
            COALESCE((SELECT SUM(m.custo_total) FROM os_materiais m WHERE m.atividade_id = a.id), 0) AS custo_materiais
        FROM os_atividades a
        WHERE a.os_id = ?
        ORDER BY a.sequencia, a.created_at
    """, (os_id,))
    rows = []
    for r in cur.fetchall():
        item = dict(r)
        classificacao = _upper(item.get('classificacao') or 'INTERNA')
        if classificacao == 'INTERNA':
            item['duracao_total_min'] = float(item.get('duracao_apontada_min') or 0)
            item['custo_total_atividade'] = float(item.get('custo_hh_apontado') or 0)
        else:
            item['duracao_total_min'] = float(item.get('duracao_min') or 0)
            item['custo_total_atividade'] = float(item.get('custo_servico_terceiro') or 0)
        rows.append(item)
    return rows


def criar_os_atividade(os_id: str, descricao: str, observacao: Optional[str] = None,
                       status: Optional[str] = None, classificacao: Optional[str] = None,
                       duracao_min: Optional[float] = None, custo_hh: Optional[float] = None,
                       custo_servico_terceiro: Optional[float] = None):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da atividade.')
    classificacao = _upper(classificacao) or 'INTERNA'
    cur = _cursor()
    cur.execute("SELECT COALESCE(MAX(sequencia), 0) + 1 FROM os_atividades WHERE os_id = ?", (os_id,))
    sequencia = int(cur.fetchone()[0] or 1)
    atividade_id = str(uuid.uuid4())
    if classificacao == 'INTERNA':
        duracao_min = 0
        custo_servico_terceiro = 0
        custo_hh = 0
    cur.execute("""
        INSERT INTO os_atividades (
            id, os_id, sequencia, descricao, status, observacao, classificacao,
            duracao_min, custo_hh, custo_servico_terceiro, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        atividade_id, os_id, sequencia, descricao, _upper(status) or 'ABERTA', _text(observacao),
        classificacao, float(duracao_min or 0), float(custo_hh or 0), float(custo_servico_terceiro or 0),
    ))
    conn.commit()
    return dict(_get_atividade_row(atividade_id))


def atualizar_os_atividade(atividade_id: str, descricao: str, observacao: Optional[str] = None,
                           status: Optional[str] = None, classificacao: Optional[str] = None,
                           duracao_min: Optional[float] = None, custo_hh: Optional[float] = None,
                           custo_servico_terceiro: Optional[float] = None):
    atual = _get_atividade_row(atividade_id)
    if not atual:
        raise ValueError('Atividade não encontrada.')
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da atividade.')
    classificacao = _upper(classificacao or atual.get('classificacao') or 'INTERNA')
    novo_status = _upper(status or atual['status'] or 'ABERTA')
    if classificacao == 'INTERNA':
        if novo_status == 'CONCLUÍDA' and contar_apontamentos_atividade(atividade_id) <= 0:
            raise ValueError('ATIVIDADE INTERNA só conclui com APONTAMENTO.')
        duracao_min = 0
        custo_hh = 0
        custo_servico_terceiro = 0
    cur = _cursor()
    cur.execute("""
        UPDATE os_atividades
        SET descricao = ?, observacao = ?, status = ?, classificacao = ?,
            duracao_min = ?, custo_hh = ?, custo_servico_terceiro = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        descricao, _text(observacao), novo_status, classificacao,
        float(duracao_min or 0), float(custo_hh or 0), float(custo_servico_terceiro or 0), atividade_id,
    ))
    conn.commit()
    return dict(_get_atividade_row(atividade_id))


def criar_os_apontamento(os_id: str, atividade_id: str, funcionario_id: str = None, funcionario_nome: str = None,
                         equipe_nome: Optional[str] = None, hora_inicio: Optional[str] = None, hora_fim: Optional[str] = None,
                         duracao_min: Optional[float] = None, descricao_servico: Optional[str] = None, observacao: Optional[str] = None,
                         data_apontamento: Optional[str] = None, usar_escala: bool = False):
    atividade = _get_atividade_row(atividade_id)
    if not atividade or str(atividade['os_id']) != str(os_id):
        raise ValueError('Atividade inválida para esta OS.')
    atividade = dict(atividade)
    if _upper(atividade.get('classificacao') or 'INTERNA') != 'INTERNA':
        raise ValueError('Apontamento só é permitido em atividade INTERNA.')
    if not funcionario_id:
        raise ValueError('Selecione o FUNCIONÁRIO.')
    return _old_criar_os_apontamento(os_id=os_id, atividade_id=atividade_id, funcionario_id=funcionario_id,
                                     funcionario_nome=funcionario_nome, equipe_nome=equipe_nome,
                                     hora_inicio=hora_inicio, hora_fim=hora_fim, duracao_min=duracao_min,
                                     descricao_servico=descricao_servico, observacao=observacao,
                                     data_apontamento=data_apontamento, usar_escala=usar_escala)


def calcular_totais_os(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT
            COALESCE((SELECT SUM(custo_total) FROM os_materiais WHERE os_id = ?), 0) AS custo_materiais,
            COALESCE((
                SELECT SUM(ap.duracao_min)
                FROM os_apontamentos ap
                JOIN os_atividades a ON a.id = ap.atividade_id
                WHERE ap.os_id = ? AND COALESCE(a.classificacao, 'INTERNA') = 'INTERNA'
            ), 0) AS hh_min_interno,
            COALESCE((
                SELECT SUM(ap.custo_hh_total)
                FROM os_apontamentos ap
                JOIN os_atividades a ON a.id = ap.atividade_id
                WHERE ap.os_id = ? AND COALESCE(a.classificacao, 'INTERNA') = 'INTERNA'
            ), 0) AS custo_hh_interno,
            COALESCE((
                SELECT SUM(COALESCE(a.duracao_min, 0))
                FROM os_atividades a
                WHERE a.os_id = ? AND COALESCE(a.classificacao, 'INTERNA') = 'SERVIÇO TERCEIRO'
            ), 0) AS duracao_terceiro_min,
            COALESCE((
                SELECT SUM(COALESCE(a.custo_servico_terceiro, 0))
                FROM os_atividades a
                WHERE a.os_id = ? AND COALESCE(a.classificacao, 'INTERNA') = 'SERVIÇO TERCEIRO'
            ), 0) AS custo_terceiro
    """, (os_id, os_id, os_id, os_id, os_id))
    row = cur.fetchone()
    custo_materiais = float(row['custo_materiais'] or 0)
    hh_min_interno = float(row['hh_min_interno'] or 0)
    custo_hh = float(row['custo_hh_interno'] or 0)
    duracao_terceiro_min = float(row['duracao_terceiro_min'] or 0)
    custo_terceiro = float(row['custo_terceiro'] or 0)
    total_min = hh_min_interno + duracao_terceiro_min
    return {
        'custo_materiais': custo_materiais,
        'hh_min': hh_min_interno,
        'duracao_terceiro_min': duracao_terceiro_min,
        'duracao_total_min': total_min,
        'hh_horas': round(hh_min_interno / 60, 2),
        'custo_hh': custo_hh,
        'custo_terceiro': custo_terceiro,
        'custo_total_os': round(custo_materiais + custo_hh + custo_terceiro, 2),
    }


# =========================
# PATCH STAGE 5 - OS UI / APONTAMENTO DIA TODO / NUMERACAO
# =========================

def proximo_numero_os() -> str:
    cur = _cursor()
    cur.execute("SELECT strftime('%Y', 'now')")
    ano = str(cur.fetchone()[0])
    cur.execute("SELECT numero FROM ordens_servico WHERE numero LIKE ? ORDER BY created_at DESC, numero DESC LIMIT 1", (f'{ano}/%',))
    row = cur.fetchone()
    sequencia = 1
    if row and row['numero']:
        try:
            sequencia = int(str(row['numero']).split('/')[-1]) + 1
        except Exception:
            sequencia = 1
    return f'{ano}/{sequencia:04d}'


def criar_os(alvo_ativo_id: str, descricao: str, tipo_os: Optional[str] = None, prioridade: Optional[str] = None,
             observacoes: Optional[str] = None, numero: Optional[str] = None, data_abertura: Optional[str] = None,
             unidade_medidor: Optional[str] = None, medidor_valor: Optional[float] = None, status: Optional[str] = None,
             justificativa_encerramento: Optional[str] = None, custo_terceiro: Optional[float] = None,
             descricao_servico_terceiro: Optional[str] = None):
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da OS.')
    alvo = _resolver_alvo_os(alvo_ativo_id)
    os_id = str(uuid.uuid4())
    numero = _upper(numero) or proximo_numero_os()
    novo_status = _upper(status) or 'ABERTA'
    cur = _cursor()
    cur.execute("""
        INSERT INTO ordens_servico (
            id, numero, origem_tipo, equipamento_id, componente_id,
            status, prioridade, tipo_os, descricao, observacoes,
            justificativa_encerramento, data_abertura, data_encerramento,
            unidade_medidor, medidor_valor, custo_terceiro, descricao_servico_terceiro,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        os_id, numero, alvo['origem_tipo'], alvo['equipamento_id'], alvo['componente_id'],
        novo_status, _upper(prioridade), _upper(tipo_os), descricao, _text(observacoes),
        _text(justificativa_encerramento), _text(data_abertura) or _agora_sql()[:10], _agora_sql() if novo_status == 'ENCERRADA' else None,
        _upper(unidade_medidor) or 'HORÍMETRO', float(medidor_valor) if medidor_valor not in ('', None) else None,
        float(custo_terceiro) if custo_terceiro not in ('', None) else 0.0, _text(descricao_servico_terceiro),
    ))
    conn.commit()
    return get_os(os_id)


def _duracao_escala_em_minutos(escala: dict) -> float:
    inicio = _hora_to_min(escala.get('inicio'))
    fim = _hora_to_min(escala.get('fim'))
    total = fim - inicio
    if total < 0:
        total += 24 * 60
    if escala.get('intervalo_inicio') and escala.get('intervalo_fim'):
        intervalo = _hora_to_min(escala.get('intervalo_fim')) - _hora_to_min(escala.get('intervalo_inicio'))
        if intervalo > 0:
            total -= intervalo
    return max(0, total)


def criar_os_apontamento(os_id: str, atividade_id: str, funcionario_id: str = None, funcionario_nome: str = None,
                         equipe_nome: Optional[str] = None, hora_inicio: Optional[str] = None, hora_fim: Optional[str] = None,
                         duracao_min: Optional[float] = None, descricao_servico: Optional[str] = None, observacao: Optional[str] = None,
                         data_apontamento: Optional[str] = None, usar_escala: bool = False):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')
    atividade = _get_atividade_row(atividade_id)
    if not atividade or str(atividade['os_id']) != str(os_id):
        raise ValueError('Atividade inválida para esta OS.')
    if _upper(atividade.get('classificacao') or 'INTERNA') != 'INTERNA':
        raise ValueError('Apontamento só é permitido em atividade INTERNA.')
    if not funcionario_id:
        raise ValueError('Selecione o FUNCIONÁRIO.')

    func = get_funcionario(funcionario_id)
    if not func:
        raise ValueError('Funcionário não encontrado.')

    data_apontamento = _text(data_apontamento) or _agora_sql()[:10]
    funcionario_nome = func.get('nome') or funcionario_nome
    equipe_nome = func.get('equipe_nome') or equipe_nome
    custo_hh_unitario = float(func.get('custo_hh') or 0)

    if usar_escala:
        escala = get_escala_para_data(funcionario_id, data_apontamento)
        if not escala:
            raise ValueError('Não existe jornada definida na escala para este dia.')
        hora_inicio = escala.get('inicio')
        hora_fim = escala.get('fim')
        duracao_min = _duracao_escala_em_minutos(escala)
    else:
        try:
            duracao_min = float(duracao_min or 0)
        except Exception:
            duracao_min = 0
        if duracao_min <= 0 and hora_inicio and hora_fim:
            ini = _hora_to_min(hora_inicio)
            fim = _hora_to_min(hora_fim)
            if fim <= ini:
                fim += 24 * 60
            duracao_min = fim - ini
        if duracao_min > 0 and not hora_fim and hora_inicio:
            total = _hora_to_min(hora_inicio) + int(round(duracao_min))
            hora_fim = f"{(total // 60) % 24:02d}:{total % 60:02d}"

    if float(duracao_min or 0) <= 0:
        raise ValueError('Informe uma DURAÇÃO válida em minutos.')

    validar_conflito_apontamento(funcionario_id, data_apontamento, hora_inicio, hora_fim)

    ap_id = str(uuid.uuid4())
    cur = _cursor()
    cur.execute("""
        INSERT INTO os_apontamentos (
            id, os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome,
            data_apontamento, hora_inicio, hora_fim, duracao_min,
            descricao_servico, observacao, modo_hora, custo_hh_unitario, custo_hh_total, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        ap_id, os_id, atividade_id, funcionario_id, _upper(funcionario_nome), func.get('equipe_id'), _upper(equipe_nome),
        data_apontamento, _text(hora_inicio), _text(hora_fim), float(duracao_min),
        _text(descricao_servico), _text(observacao), 'ESCALA' if usar_escala else 'MANUAL',
        custo_hh_unitario, round((float(duracao_min) / 60.0) * custo_hh_unitario, 2),
    ))
    if _upper(atividade['status']) == 'ABERTA':
        cur.execute("UPDATE os_atividades SET status = 'EM EXECUÇÃO', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (atividade_id,))
    conn.commit()
    return ap_id


def excluir_os_apontamento(apontamento_id: str):
    cur = _cursor()
    cur.execute("SELECT atividade_id FROM os_apontamentos WHERE id = ?", (apontamento_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Apontamento não encontrado.')
    atividade_id = row['atividade_id']
    cur.execute("DELETE FROM os_apontamentos WHERE id = ?", (apontamento_id,))
    conn.commit()
    if contar_apontamentos_atividade(atividade_id) <= 0:
        cur = _cursor()
        cur.execute("UPDATE os_atividades SET status = 'ABERTA', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'EM EXECUÇÃO'", (atividade_id,))
        conn.commit()


def excluir_os_atividade(atividade_id: str):
    atual = _get_atividade_row(atividade_id)
    if not atual:
        raise ValueError('Atividade não encontrada.')
    cur = _cursor()
    cur.execute("DELETE FROM os_atividades WHERE id = ?", (atividade_id,))
    conn.commit()

# =========================
# FINAL PATCH - APONTAMENTO/ESCALA/TIME INPUT
# =========================

def _row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return row


def get_escala_para_data(funcionario_id: str, data_iso: str):
    func = get_funcionario(funcionario_id)
    if not func or not func.get('escala_id'):
        return None
    cur = _cursor()
    cur.execute("SELECT * FROM escalas WHERE id = ?", (func['escala_id'],))
    esc = _row_to_dict(cur.fetchone())
    if not esc:
        return None
    import datetime as _dt
    try:
        dt = _dt.datetime.strptime(str(data_iso), '%Y-%m-%d')
    except Exception:
        return None
    mapa = ['seg', 'ter', 'qua', 'qui', 'sex', 'sab', 'dom']
    chave = mapa[dt.weekday()]
    inicio = esc.get(f'{chave}_inicio')
    fim = esc.get(f'{chave}_fim')
    if not inicio or not fim:
        return None
    return {
        'inicio': inicio,
        'fim': fim,
        'intervalo_inicio': esc.get(f'{chave}_int_inicio'),
        'intervalo_fim': esc.get(f'{chave}_int_fim'),
        'nome': esc.get('nome'),
    }


def proximo_numero_os() -> str:
    cur = _cursor()
    cur.execute("SELECT strftime('%Y', 'now')")
    ano = str(cur.fetchone()[0])
    cur.execute("SELECT numero FROM ordens_servico WHERE numero LIKE ? ORDER BY numero DESC LIMIT 1", (f'{ano}/%',))
    row = cur.fetchone()
    sequencia = 1
    if row and row['numero']:
        try:
            sequencia = int(str(row['numero']).split('/')[-1]) + 1
        except Exception:
            sequencia = 1
    return f'{ano}/{sequencia:04d}'


def criar_os_apontamento(os_id: str, atividade_id: str, funcionario_id: str = None, funcionario_nome: str = None,
                         equipe_nome: Optional[str] = None, hora_inicio: Optional[str] = None, hora_fim: Optional[str] = None,
                         duracao_min: Optional[float] = None, descricao_servico: Optional[str] = None, observacao: Optional[str] = None,
                         data_apontamento: Optional[str] = None, usar_escala: bool = False):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')
    atividade = _row_to_dict(_get_atividade_row(atividade_id))
    if not atividade or str(atividade['os_id']) != str(os_id):
        raise ValueError('Atividade inválida para esta OS.')
    if _upper(atividade.get('classificacao') or 'INTERNA') != 'INTERNA':
        raise ValueError('Apontamento só é permitido em atividade INTERNA.')
    if not funcionario_id:
        raise ValueError('Selecione o FUNCIONÁRIO.')

    func = get_funcionario(funcionario_id)
    if not func:
        raise ValueError('Funcionário não encontrado.')

    data_apontamento = _text(data_apontamento) or _agora_sql()[:10]
    funcionario_nome = func.get('nome') or funcionario_nome
    equipe_nome = func.get('equipe_nome') or equipe_nome
    custo_hh_unitario = float(func.get('custo_hh') or 0)

    if usar_escala:
        escala = get_escala_para_data(funcionario_id, data_apontamento)
        if not escala:
            raise ValueError('Não existe jornada definida na escala para este dia.')
        hora_inicio = _text(escala.get('inicio'))
        hora_fim = _text(escala.get('fim'))
        duracao_min = float(_duracao_escala_em_minutos(escala))
    else:
        try:
            duracao_min = float(duracao_min or 0)
        except Exception:
            duracao_min = 0
        hora_inicio = _text(hora_inicio)
        hora_fim = _text(hora_fim)
        if duracao_min <= 0 and hora_inicio and hora_fim:
            ini = _hora_to_min(hora_inicio)
            fim = _hora_to_min(hora_fim)
            if fim <= ini:
                fim += 24 * 60
            duracao_min = float(fim - ini)
        if duracao_min > 0 and not hora_fim and hora_inicio:
            total = _hora_to_min(hora_inicio) + int(round(duracao_min))
            hora_fim = f"{(total // 60) % 24:02d}:{total % 60:02d}"

    if float(duracao_min or 0) <= 0:
        raise ValueError('Informe uma DURAÇÃO válida.')
    if not hora_inicio:
        raise ValueError('Informe a HORA INÍCIO.')
    if not hora_fim:
        total = _hora_to_min(hora_inicio) + int(round(float(duracao_min or 0)))
        hora_fim = f"{(total // 60) % 24:02d}:{total % 60:02d}"

    validar_conflito_apontamento(funcionario_id, data_apontamento, hora_inicio, hora_fim)

    ap_id = str(uuid.uuid4())
    custo_total = round((float(duracao_min) / 60.0) * custo_hh_unitario, 2)
    cur = _cursor()
    cur.execute(
        """
        INSERT INTO os_apontamentos (
            id, os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome,
            data_apontamento, hora_inicio, hora_fim, duracao_min,
            descricao_servico, observacao, modo_hora, custo_hh_unitario, custo_hh_total, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            ap_id, os_id, atividade_id, funcionario_id, _upper(funcionario_nome), func.get('equipe_id'), _upper(equipe_nome),
            data_apontamento, hora_inicio, hora_fim, float(duracao_min),
            _text(descricao_servico), _text(observacao), 'ESCALA' if usar_escala else 'MANUAL',
            custo_hh_unitario, custo_total,
        )
    )
    if _upper(atividade.get('status') or 'ABERTA') == 'ABERTA':
        cur.execute("UPDATE os_atividades SET status = 'EM EXECUÇÃO', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (atividade_id,))
    conn.commit()
    return ap_id


# ===== PATCH APONTAMENTOS MANUAIS/ESCALA =====
def _duracao_periodo(inicio: str, fim: str) -> int:
    mi = _hora_to_min(inicio)
    mf = _hora_to_min(fim)
    if mf < mi:
        mf += 24 * 60
    return max(0, mf - mi)


def _insert_apontamento_base(
    os_id: str,
    atividade_id: str,
    funcionario_id: str,
    funcionario_nome: str,
    equipe_id: str,
    equipe_nome: str,
    data_apontamento: str,
    hora_inicio: str,
    hora_fim: str,
    duracao_min: float,
    descricao_servico: str,
    observacao: str,
    modo_hora: str,
):
    atividade = _get_atividade_row(atividade_id)
    if not atividade or str(atividade['os_id']) != str(os_id):
        raise ValueError('Atividade inválida para esta OS.')
    ap_id = str(uuid.uuid4())
    cur = _cursor()
    cur.execute(
        """
        INSERT INTO os_apontamentos (
            id, os_id, atividade_id, funcionario_nome, equipe_nome,
            data_apontamento, hora_inicio, hora_fim, duracao_min,
            descricao_servico, observacao, created_at, funcionario_id, equipe_id, modo_hora
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
        """,
        (
            ap_id, os_id, atividade_id, _upper(funcionario_nome), _upper(equipe_nome),
            _text(data_apontamento) or _agora_sql()[:10], _text(hora_inicio), _text(hora_fim), float(duracao_min or 0),
            _text(descricao_servico), _text(observacao), funcionario_id or None, equipe_id or None, modo_hora,
        ),
    )
    if _upper(atividade['status']) == 'ABERTA':
        cur.execute("UPDATE os_atividades SET status = 'EM EXECUÇÃO', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (atividade_id,))
    conn.commit()
    return ap_id


def listar_os_apontamentos(os_id: str):
    cur = _cursor()
    cur.execute(
        """
        SELECT ap.*, atv.descricao AS atividade_descricao,
               COALESCE(ROUND((ap.duracao_min / 60.0) * COALESCE(f.custo_hh, 0), 2), 0) AS custo_hh_total
        FROM os_apontamentos ap
        JOIN os_atividades atv ON atv.id = ap.atividade_id
        LEFT JOIN funcionarios f ON f.id = ap.funcionario_id
        WHERE ap.os_id = ?
        ORDER BY ap.data_apontamento DESC, ap.hora_inicio DESC, ap.created_at DESC
        """,
        (os_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def criar_os_apontamento(
    os_id: str,
    atividade_id: str,
    funcionario_id: str = None,
    funcionario_nome: str = None,
    equipe_nome: Optional[str] = None,
    hora_inicio: Optional[str] = None,
    hora_fim: Optional[str] = None,
    duracao_min: Optional[float] = None,
    descricao_servico: Optional[str] = None,
    observacao: Optional[str] = None,
    data_apontamento: Optional[str] = None,
    usar_escala: bool = False,
):
    if not _get_os_row(os_id):
        raise ValueError('OS não encontrada.')
    func = get_funcionario(funcionario_id) if funcionario_id else None
    if funcionario_id and not func:
        raise ValueError('Funcionário não encontrado.')
    funcionario_nome = (func or {}).get('nome') or funcionario_nome
    equipe_nome = (func or {}).get('equipe_nome') or equipe_nome
    equipe_id = (func or {}).get('equipe_id')
    data_apontamento = _text(data_apontamento) or _agora_sql()[:10]
    if not _upper(funcionario_nome):
        raise ValueError('Informe o FUNCIONÁRIO do apontamento.')

    if usar_escala:
        if not funcionario_id:
            raise ValueError('Selecione o funcionário para usar a escala.')
        escala = get_escala_para_data(funcionario_id, data_apontamento)
        if not escala:
            raise ValueError('Não existe jornada definida na escala para este dia.')
        inicio = _text(escala.get('inicio'))
        fim = _text(escala.get('fim'))
        int_ini = _text(escala.get('intervalo_inicio'))
        int_fim = _text(escala.get('intervalo_fim'))
        criados = []
        if int_ini and int_fim and _hora_to_min(int_fim) > _hora_to_min(int_ini):
            p1 = _duracao_periodo(inicio, int_ini)
            p2 = _duracao_periodo(int_fim, fim)
            if p1 > 0:
                validar_conflito_apontamento(funcionario_id, data_apontamento, inicio, int_ini)
                criados.append(_insert_apontamento_base(os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome, data_apontamento, inicio, int_ini, p1, descricao_servico, observacao, 'ESCALA'))
            if p2 > 0:
                validar_conflito_apontamento(funcionario_id, data_apontamento, int_fim, fim)
                criados.append(_insert_apontamento_base(os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome, data_apontamento, int_fim, fim, p2, descricao_servico, observacao, 'ESCALA'))
        else:
            total = _duracao_periodo(inicio, fim)
            validar_conflito_apontamento(funcionario_id, data_apontamento, inicio, fim)
            criados.append(_insert_apontamento_base(os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome, data_apontamento, inicio, fim, total, descricao_servico, observacao, 'ESCALA'))
        return criados[0] if len(criados) == 1 else criados

    hora_inicio = _text(hora_inicio)
    hora_fim = _text(hora_fim)
    try:
        duracao_min = float(duracao_min or 0)
    except Exception:
        duracao_min = 0
    if not hora_inicio:
        raise ValueError('Informe a HORA INÍCIO.')
    if hora_fim:
        duracao_min = _duracao_periodo(hora_inicio, hora_fim)
    elif duracao_min > 0:
        total = _hora_to_min(hora_inicio) + int(duracao_min)
        hora_fim = f"{(total // 60) % 24:02d}:{total % 60:02d}"
    else:
        raise ValueError('Informe a HORA FIM ou uma DURAÇÃO válida.')
    validar_conflito_apontamento(funcionario_id, data_apontamento, hora_inicio, hora_fim)
    return _insert_apontamento_base(os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome, data_apontamento, hora_inicio, hora_fim, duracao_min, descricao_servico, observacao, 'MANUAL')


def atualizar_os_apontamento(
    apontamento_id: str,
    os_id: str,
    atividade_id: str,
    funcionario_id: str = None,
    funcionario_nome: str = None,
    equipe_nome: Optional[str] = None,
    hora_inicio: Optional[str] = None,
    hora_fim: Optional[str] = None,
    duracao_min: Optional[float] = None,
    descricao_servico: Optional[str] = None,
    observacao: Optional[str] = None,
    data_apontamento: Optional[str] = None,
    usar_escala: bool = False,
):
    atual = get_os_apontamento(apontamento_id)
    if not atual:
        raise ValueError('Apontamento não encontrado.')
    cur = _cursor()
    if _upper(atual.get('modo_hora')) == 'ESCALA':
        cur.execute(
            "DELETE FROM os_apontamentos WHERE os_id = ? AND atividade_id = ? AND ((funcionario_id = ?) OR (funcionario_id IS NULL AND ? IS NULL)) AND data_apontamento = ? AND modo_hora = 'ESCALA' AND descricao_servico = ? AND observacao = ?",
            (atual['os_id'], atual['atividade_id'], atual.get('funcionario_id'), atual.get('funcionario_id'), atual.get('data_apontamento'), atual.get('descricao_servico') or '', atual.get('observacao') or ''),
        )
    else:
        cur.execute("DELETE FROM os_apontamentos WHERE id = ?", (apontamento_id,))
    conn.commit()
    return criar_os_apontamento(os_id=os_id, atividade_id=atividade_id, funcionario_id=funcionario_id, funcionario_nome=funcionario_nome, equipe_nome=equipe_nome, hora_inicio=hora_inicio, hora_fim=hora_fim, duracao_min=duracao_min, descricao_servico=descricao_servico, observacao=observacao, data_apontamento=data_apontamento, usar_escala=usar_escala)


def get_os_apontamento(apontamento_id: str):
    cur = _cursor()
    cur.execute("SELECT * FROM os_apontamentos WHERE id = ?", (apontamento_id,))
    row = cur.fetchone()
    return dict(row) if row else None


# =========================
# FINAL PATCH - TOTAIS OS / CUSTO HH / APONTAMENTO MANUAL
# =========================

def _merge_day_intervals(intervalos):
    blocos = []
    for inicio, fim in intervalos:
        if not inicio or not fim:
            continue
        ini = _hora_to_min(str(inicio))
        fim_min = _hora_to_min(str(fim))
        if fim_min < ini:
            fim_min += 24 * 60
        blocos.append((ini, fim_min))
    if not blocos:
        return 0.0
    blocos.sort()
    total = 0
    atual_ini, atual_fim = blocos[0]
    for ini, fim in blocos[1:]:
        if ini <= atual_fim:
            atual_fim = max(atual_fim, fim)
        else:
            total += atual_fim - atual_ini
            atual_ini, atual_fim = ini, fim
    total += atual_fim - atual_ini
    return float(total)


def _duracao_apontamentos_uniao_os(os_id: str) -> float:
    cur = _cursor()
    cur.execute(
        """
        SELECT data_apontamento, hora_inicio, hora_fim
        FROM os_apontamentos
        WHERE os_id = ?
          AND COALESCE(hora_inicio, '') <> ''
          AND COALESCE(hora_fim, '') <> ''
        ORDER BY data_apontamento, hora_inicio
        """,
        (os_id,),
    )
    por_dia = {}
    for row in cur.fetchall():
        item = dict(row)
        dia = str(item.get('data_apontamento') or '')
        por_dia.setdefault(dia, []).append((item.get('hora_inicio'), item.get('hora_fim')))
    total = 0.0
    for intervalos in por_dia.values():
        total += _merge_day_intervals(intervalos)
    return float(total)


def calcular_totais_os(os_id: str):
    cur = _cursor()
    cur.execute(
        """
        SELECT
            COALESCE((SELECT SUM(custo_total) FROM os_materiais WHERE os_id = ?), 0) AS custo_materiais,
            COALESCE((SELECT SUM(COALESCE(ap.custo_hh_total, ROUND((ap.duracao_min / 60.0) * COALESCE(f.custo_hh, 0), 2), 0)) FROM os_apontamentos ap LEFT JOIN funcionarios f ON f.id = ap.funcionario_id WHERE ap.os_id = ?), 0) AS custo_hh,
            COALESCE((SELECT SUM(COALESCE(a.duracao_min, 0)) FROM os_atividades a WHERE a.os_id = ? AND COALESCE(a.classificacao, 'INTERNA') = 'SERVIÇO TERCEIRO'), 0) AS duracao_terceiro_min,
            COALESCE((SELECT SUM(COALESCE(a.custo_servico_terceiro, 0)) FROM os_atividades a WHERE a.os_id = ? AND COALESCE(a.classificacao, 'INTERNA') = 'SERVIÇO TERCEIRO'), 0) AS custo_terceiro
        """,
        (os_id, os_id, os_id, os_id),
    )
    row = cur.fetchone()
    custo_materiais = float(row['custo_materiais'] or 0)
    custo_hh = float(row['custo_hh'] or 0)
    duracao_interna_uniao = _duracao_apontamentos_uniao_os(os_id)
    duracao_terceiro_min = float(row['duracao_terceiro_min'] or 0)
    custo_terceiro = float(row['custo_terceiro'] or 0)
    duracao_total_min = float(duracao_interna_uniao + duracao_terceiro_min)
    return {
        'custo_materiais': custo_materiais,
        'hh_min': duracao_interna_uniao,
        'duracao_terceiro_min': duracao_terceiro_min,
        'duracao_total_min': duracao_total_min,
        'hh_horas': round(duracao_interna_uniao / 60.0, 2),
        'custo_hh': custo_hh,
        'custo_terceiro': custo_terceiro,
        'custo_total_os': round(custo_materiais + custo_hh + custo_terceiro, 2),
    }


def _insert_apontamento_base(
    os_id: str,
    atividade_id: str,
    funcionario_id: str,
    funcionario_nome: str,
    equipe_id: str,
    equipe_nome: str,
    data_apontamento: str,
    hora_inicio: str,
    hora_fim: str,
    duracao_min: float,
    descricao_servico: str,
    observacao: str,
    modo_hora: str,
):
    atividade = _get_atividade_row(atividade_id)
    if not atividade or str(atividade['os_id']) != str(os_id):
        raise ValueError('Atividade inválida para esta OS.')
    func = get_funcionario(funcionario_id) if funcionario_id else None
    custo_hh_unitario = float((func or {}).get('custo_hh') or 0)
    custo_hh_total = round((float(duracao_min or 0) / 60.0) * custo_hh_unitario, 2)
    ap_id = str(uuid.uuid4())
    cur = _cursor()
    cur.execute(
        """
        INSERT INTO os_apontamentos (
            id, os_id, atividade_id, funcionario_nome, equipe_nome,
            data_apontamento, hora_inicio, hora_fim, duracao_min,
            descricao_servico, observacao, created_at, funcionario_id, equipe_id, modo_hora,
            custo_hh_unitario, custo_hh_total
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)
        """,
        (
            ap_id, os_id, atividade_id, _upper(funcionario_nome), _upper(equipe_nome),
            _text(data_apontamento) or _agora_sql()[:10], _text(hora_inicio), _text(hora_fim), float(duracao_min or 0),
            _text(descricao_servico), _text(observacao), funcionario_id or None, equipe_id or None, modo_hora,
            custo_hh_unitario, custo_hh_total,
        ),
    )
    if _upper(atividade['status']) == 'ABERTA':
        cur.execute("UPDATE os_atividades SET status = 'EM EXECUÇÃO', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (atividade_id,))
    conn.commit()
    return ap_id


def listar_os_apontamentos(os_id: str):
    cur = _cursor()
    cur.execute(
        """
        SELECT ap.*, atv.descricao AS atividade_descricao,
               COALESCE(ap.custo_hh_total, ROUND((ap.duracao_min / 60.0) * COALESCE(f.custo_hh, 0), 2), 0) AS custo_hh_total
        FROM os_apontamentos ap
        JOIN os_atividades atv ON atv.id = ap.atividade_id
        LEFT JOIN funcionarios f ON f.id = ap.funcionario_id
        WHERE ap.os_id = ?
        ORDER BY ap.data_apontamento DESC, ap.hora_inicio DESC, ap.created_at DESC
        """,
        (os_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def atualizar_os_apontamento(
    apontamento_id: str,
    os_id: str,
    atividade_id: str,
    funcionario_id: str = None,
    funcionario_nome: str = None,
    equipe_nome: Optional[str] = None,
    hora_inicio: Optional[str] = None,
    hora_fim: Optional[str] = None,
    duracao_min: Optional[float] = None,
    descricao_servico: Optional[str] = None,
    observacao: Optional[str] = None,
    data_apontamento: Optional[str] = None,
    usar_escala: bool = False,
):
    atual = get_os_apontamento(apontamento_id)
    if not atual:
        raise ValueError('Apontamento não encontrado.')
    cur = _cursor()
    if _upper(atual.get('modo_hora')) == 'ESCALA':
        cur.execute(
            "DELETE FROM os_apontamentos WHERE os_id = ? AND atividade_id = ? AND ((funcionario_id = ?) OR (funcionario_id IS NULL AND ? IS NULL)) AND data_apontamento = ? AND modo_hora = 'ESCALA' AND COALESCE(descricao_servico,'') = ? AND COALESCE(observacao,'') = ?",
            (atual['os_id'], atual['atividade_id'], atual.get('funcionario_id'), atual.get('funcionario_id'), atual.get('data_apontamento'), atual.get('descricao_servico') or '', atual.get('observacao') or ''),
        )
    else:
        cur.execute("DELETE FROM os_apontamentos WHERE id = ?", (apontamento_id,))
    conn.commit()
    return criar_os_apontamento(
        os_id=os_id,
        atividade_id=atividade_id,
        funcionario_id=funcionario_id,
        funcionario_nome=funcionario_nome,
        equipe_nome=equipe_nome,
        hora_inicio=hora_inicio,
        hora_fim=hora_fim,
        duracao_min=duracao_min,
        descricao_servico=descricao_servico,
        observacao=observacao,
        data_apontamento=data_apontamento,
        usar_escala=usar_escala,
    )


# ===== ajustes finais OS / apontamentos terceiro / OS em execução =====
def _ensure_os_apontamentos_empresa_terceira():
    cols = _get_columns('os_apontamentos')
    cur = _cursor()
    if 'empresa_terceira' not in cols:
        cur.execute("ALTER TABLE os_apontamentos ADD COLUMN empresa_terceira TEXT NULL")
        conn.commit()

_ensure_os_apontamentos_empresa_terceira()


def _atividade_classificacao(atividade_id: str) -> str:
    cur = _cursor()
    cur.execute("SELECT classificacao FROM os_atividades WHERE id = ?", (atividade_id,))
    row = cur.fetchone()
    return _upper((dict(row) if row else {}).get('classificacao') or 'INTERNA')


def _insert_apontamento_base(
    os_id: str,
    atividade_id: str,
    funcionario_id: str,
    funcionario_nome: str,
    equipe_id: str,
    equipe_nome: str,
    data_apontamento: str,
    hora_inicio: str,
    hora_fim: str,
    duracao_min: float,
    descricao_servico: str,
    observacao: str,
    modo_hora: str,
    empresa_terceira: str = None,
):
    atividade = _row_to_dict(_get_atividade_row(atividade_id))
    if not atividade or str(atividade['os_id']) != str(os_id):
        raise ValueError('Atividade inválida para esta OS.')
    classificacao = _upper(atividade.get('classificacao') or 'INTERNA')
    func = get_funcionario(funcionario_id) if (funcionario_id and classificacao == 'INTERNA') else None
    custo_hh_unitario = float((func or {}).get('custo_hh') or 0)
    custo_hh_total = round((float(duracao_min or 0) / 60.0) * custo_hh_unitario, 2) if classificacao == 'INTERNA' else 0.0
    ap_id = str(uuid.uuid4())
    cur = _cursor()
    cur.execute(
        """
        INSERT INTO os_apontamentos (
            id, os_id, atividade_id, funcionario_nome, equipe_nome,
            data_apontamento, hora_inicio, hora_fim, duracao_min,
            descricao_servico, observacao, created_at, funcionario_id, equipe_id, modo_hora,
            custo_hh_unitario, custo_hh_total, empresa_terceira
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
        """,
        (
            ap_id, os_id, atividade_id, _upper(funcionario_nome), _upper(equipe_nome),
            _text(data_apontamento) or _agora_sql()[:10], _text(hora_inicio), _text(hora_fim), float(duracao_min or 0),
            _text(descricao_servico), _text(observacao), funcionario_id or None, equipe_id or None, modo_hora,
            custo_hh_unitario, custo_hh_total, _upper(empresa_terceira),
        ),
    )
    if _upper(atividade['status']) == 'ABERTA':
        cur.execute("UPDATE os_atividades SET status = 'EM EXECUÇÃO', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (atividade_id,))
    os_row = _row_to_dict(_get_os_row(os_id))
    if os_row and _upper(os_row.get('status') or 'ABERTA') == 'ABERTA':
        cur.execute("UPDATE ordens_servico SET status = 'EM EXECUÇÃO', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (os_id,))
    conn.commit()
    return ap_id


def listar_os_apontamentos(os_id: str):
    cur = _cursor()
    cur.execute(
        """
        SELECT ap.*, atv.descricao AS atividade_descricao,
               COALESCE(ap.custo_hh_total, ROUND((ap.duracao_min / 60.0) * COALESCE(f.custo_hh, 0), 2), 0) AS custo_hh_total
        FROM os_apontamentos ap
        JOIN os_atividades atv ON atv.id = ap.atividade_id
        LEFT JOIN funcionarios f ON f.id = ap.funcionario_id
        WHERE ap.os_id = ?
        ORDER BY ap.data_apontamento DESC, ap.hora_inicio DESC, ap.created_at DESC
        """,
        (os_id,),
    )
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        if _upper(item.get('empresa_terceira')) and not item.get('funcionario_nome'):
            item['funcionario_nome'] = item.get('empresa_terceira')
        rows.append(item)
    return rows


def criar_os_apontamento(
    os_id: str,
    atividade_id: str,
    funcionario_id: str = None,
    funcionario_nome: str = None,
    equipe_nome: Optional[str] = None,
    hora_inicio: Optional[str] = None,
    hora_fim: Optional[str] = None,
    duracao_min: Optional[float] = None,
    descricao_servico: Optional[str] = None,
    observacao: Optional[str] = None,
    data_apontamento: Optional[str] = None,
    usar_escala: bool = False,
    empresa_terceira: Optional[str] = None,
):
    atividade = _row_to_dict(_get_atividade_row(atividade_id))
    if not atividade or str(atividade['os_id']) != str(os_id):
        raise ValueError('Atividade inválida para esta OS.')
    classificacao = _upper(atividade.get('classificacao') or 'INTERNA')
    data_apontamento = _text(data_apontamento) or _agora_sql()[:10]
    if classificacao == 'SERVIÇO TERCEIRO':
        empresa_terceira = _upper(empresa_terceira or funcionario_nome)
        if not empresa_terceira:
            raise ValueError('Informe a EMPRESA do apontamento.')
        inicio = _text(hora_inicio)
        fim = _text(hora_fim)
        if not inicio:
            raise ValueError('Informe a HORA INÍCIO.')
        if not fim and duracao_min is None:
            raise ValueError('Informe a HORA FIM ou a DURAÇÃO.')
        if not fim and duracao_min is not None:
            mi = _hora_to_min(inicio)
            total = int(float(duracao_min or 0))
            mf = mi + total
            fim = f"{(mf // 60) % 24:02d}:{mf % 60:02d}"
        if duracao_min is None:
            mi = _hora_to_min(inicio)
            mf = _hora_to_min(fim)
            if mf < mi:
                mf += 24 * 60
            duracao_min = mf - mi
        return _insert_apontamento_base(os_id, atividade_id, None, empresa_terceira, None, None, data_apontamento, inicio, fim, float(duracao_min or 0), descricao_servico, observacao, 'MANUAL', empresa_terceira=empresa_terceira)

    # interna
    if not funcionario_id:
        raise ValueError('Informe o FUNCIONÁRIO do apontamento.')
    func = get_funcionario(funcionario_id)
    if not func:
        raise ValueError('Funcionário não encontrado.')
    funcionario_nome = _upper(func.get('nome') or funcionario_nome)
    equipe_id = func.get('equipe_id')
    equipe_nome = _upper(func.get('equipe_nome') or equipe_nome)
    if usar_escala:
        escala = get_escala_para_data(funcionario_id, data_apontamento)
        if not escala:
            raise ValueError('Não existe jornada definida na escala para este dia.')
        inicio = str(escala.get('inicio') or '')
        fim = str(escala.get('fim') or '')
        int_ini = str(escala.get('intervalo_inicio') or '')
        int_fim = str(escala.get('intervalo_fim') or '')
        criados = []
        if int_ini and int_fim:
            p1 = max(0, _hora_to_min(int_ini) - _hora_to_min(inicio))
            p2 = max(0, _hora_to_min(fim) - _hora_to_min(int_fim))
            if p1 > 0:
                validar_conflito_apontamento(funcionario_id, data_apontamento, inicio, int_ini)
                criados.append(_insert_apontamento_base(os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome, data_apontamento, inicio, int_ini, p1, descricao_servico, observacao, 'ESCALA'))
            if p2 > 0:
                validar_conflito_apontamento(funcionario_id, data_apontamento, int_fim, fim)
                criados.append(_insert_apontamento_base(os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome, data_apontamento, int_fim, fim, p2, descricao_servico, observacao, 'ESCALA'))
            return criados[0] if len(criados) == 1 else (criados[0] if criados else None)
        total = max(0, _hora_to_min(fim) - _hora_to_min(inicio))
        validar_conflito_apontamento(funcionario_id, data_apontamento, inicio, fim)
        return _insert_apontamento_base(os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome, data_apontamento, inicio, fim, total, descricao_servico, observacao, 'ESCALA')

    hora_inicio = _text(hora_inicio)
    hora_fim = _text(hora_fim)
    if not hora_inicio:
        raise ValueError('Informe a HORA INÍCIO.')
    if not hora_fim and duracao_min is None:
        raise ValueError('Informe a HORA FIM ou a DURAÇÃO.')
    if not hora_fim and duracao_min is not None:
        mi = _hora_to_min(hora_inicio)
        total = int(float(duracao_min or 0))
        mf = mi + total
        hora_fim = f"{(mf // 60) % 24:02d}:{mf % 60:02d}"
    if duracao_min is None:
        mi = _hora_to_min(hora_inicio)
        mf = _hora_to_min(hora_fim)
        if mf < mi:
            mf += 24 * 60
        duracao_min = mf - mi
    validar_conflito_apontamento(funcionario_id, data_apontamento, hora_inicio, hora_fim)
    return _insert_apontamento_base(os_id, atividade_id, funcionario_id, funcionario_nome, equipe_id, equipe_nome, data_apontamento, hora_inicio, hora_fim, float(duracao_min or 0), descricao_servico, observacao, 'MANUAL')


def atualizar_os_apontamento(
    apontamento_id: str,
    os_id: str,
    atividade_id: str,
    funcionario_id: str = None,
    funcionario_nome: str = None,
    equipe_nome: Optional[str] = None,
    hora_inicio: Optional[str] = None,
    hora_fim: Optional[str] = None,
    duracao_min: Optional[float] = None,
    descricao_servico: Optional[str] = None,
    observacao: Optional[str] = None,
    data_apontamento: Optional[str] = None,
    usar_escala: bool = False,
    empresa_terceira: Optional[str] = None,
):
    atual = get_os_apontamento(apontamento_id)
    if not atual:
        raise ValueError('Apontamento não encontrado.')
    cur = _cursor()
    if _upper(atual.get('modo_hora')) == 'ESCALA':
        cur.execute(
            "DELETE FROM os_apontamentos WHERE os_id = ? AND atividade_id = ? AND ((funcionario_id = ?) OR (funcionario_id IS NULL AND ? IS NULL)) AND data_apontamento = ? AND modo_hora = 'ESCALA' AND COALESCE(descricao_servico,'') = ? AND COALESCE(observacao,'') = ?",
            (atual['os_id'], atual['atividade_id'], atual.get('funcionario_id'), atual.get('funcionario_id'), atual.get('data_apontamento'), atual.get('descricao_servico') or '', atual.get('observacao') or ''),
        )
    else:
        cur.execute("DELETE FROM os_apontamentos WHERE id = ?", (apontamento_id,))
    conn.commit()
    return criar_os_apontamento(os_id=os_id, atividade_id=atividade_id, funcionario_id=funcionario_id, funcionario_nome=funcionario_nome, equipe_nome=equipe_nome, hora_inicio=hora_inicio, hora_fim=hora_fim, duracao_min=duracao_min, descricao_servico=descricao_servico, observacao=observacao, data_apontamento=data_apontamento, usar_escala=usar_escala, empresa_terceira=empresa_terceira)


def listar_os_atividades(os_id: str):
    cur = _cursor()
    cur.execute("""
        SELECT
            a.*,
            COALESCE((SELECT COUNT(*) FROM os_apontamentos ap WHERE ap.atividade_id = a.id), 0) AS total_apontamentos,
            COALESCE((SELECT SUM(ap.duracao_min) FROM os_apontamentos ap WHERE ap.atividade_id = a.id), 0) AS duracao_apontada_min,
            COALESCE((SELECT SUM(ap.custo_hh_total) FROM os_apontamentos ap WHERE ap.atividade_id = a.id), 0) AS custo_hh_apontado,
            COALESCE((SELECT COUNT(*) FROM os_materiais m WHERE m.atividade_id = a.id), 0) AS total_materiais,
            COALESCE((SELECT SUM(m.custo_total) FROM os_materiais m WHERE m.atividade_id = a.id), 0) AS custo_materiais
        FROM os_atividades a
        WHERE a.os_id = ?
        ORDER BY a.sequencia, a.created_at
    """, (os_id,))
    rows = []
    for r in cur.fetchall():
        item = dict(r)
        classificacao = _upper(item.get('classificacao') or 'INTERNA')
        if classificacao == 'INTERNA':
            item['duracao_total_min'] = float(item.get('duracao_apontada_min') or 0)
            item['custo_total_atividade'] = float(item.get('custo_hh_apontado') or 0)
        else:
            apontada = float(item.get('duracao_apontada_min') or 0)
            item['duracao_total_min'] = apontada if apontada > 0 else float(item.get('duracao_min') or 0)
            item['custo_total_atividade'] = float(item.get('custo_servico_terceiro') or 0)
        rows.append(item)
    return rows


def atualizar_os(os_id: str, alvo_ativo_id: str, descricao: str, tipo_os: Optional[str] = None, prioridade: Optional[str] = None,
                 observacoes: Optional[str] = None, status: Optional[str] = None, justificativa_encerramento: Optional[str] = None,
                 data_abertura: Optional[str] = None, unidade_medidor: Optional[str] = None, medidor_valor: Optional[float] = None,
                 custo_terceiro: Optional[float] = None, descricao_servico_terceiro: Optional[str] = None,
                 data_encerramento: Optional[str] = None, usuario_encerramento: Optional[str] = None, usuario_encerramento_id: Optional[str] = None):
    atual = _get_os_row(os_id)
    if not atual:
        raise ValueError('OS não encontrada.')
    descricao = _upper(descricao)
    if not descricao:
        raise ValueError('Informe a DESCRIÇÃO da OS.')
    alvo = _resolver_alvo_os(alvo_ativo_id)
    novo_status = _upper(status or atual['status'] or 'ABERTA')
    justificativa_encerramento = _text(justificativa_encerramento)

    if novo_status == 'ENCERRADA':
        validar_encerramento_os(os_id, justificativa_encerramento)
        data_encerramento_final = _text(data_encerramento) or _agora_sql()[:10]
        usuario_encerramento_final = _upper(usuario_encerramento) or _upper(atual['usuario_encerramento'])
        usuario_encerramento_id_final = _text(usuario_encerramento_id) or _text(atual['usuario_encerramento_id'])
    else:
        data_encerramento_final = None
        usuario_encerramento_final = None
        usuario_encerramento_id_final = None

    cur = _cursor()
    cur.execute("""
        UPDATE ordens_servico
        SET origem_tipo = ?, equipamento_id = ?, componente_id = ?, descricao = ?, tipo_os = ?, prioridade = ?,
            observacoes = ?, data_abertura = ?, unidade_medidor = ?, medidor_valor = ?, status = ?,
            justificativa_encerramento = ?, data_encerramento = ?, usuario_encerramento = ?, usuario_encerramento_id = ?,
            custo_terceiro = ?, descricao_servico_terceiro = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        alvo['origem_tipo'], alvo['equipamento_id'], alvo['componente_id'], descricao, _upper(tipo_os), _upper(prioridade),
        _text(observacoes), _text(data_abertura) or _text(atual['data_abertura']), _upper(unidade_medidor) or _upper(atual['unidade_medidor']) or 'HORÍMETRO',
        float(medidor_valor) if medidor_valor not in ('', None) else atual['medidor_valor'], novo_status,
        justificativa_encerramento, data_encerramento_final, usuario_encerramento_final, usuario_encerramento_id_final,
        float(custo_terceiro) if custo_terceiro not in ('', None) else float(atual['custo_terceiro'] or 0), _text(descricao_servico_terceiro),
        os_id,
    ))
    conn.commit()
    return get_os(os_id)


# =========================
# DASHBOARD
# =========================

def _dashboard_periodo_equipamento_filter(periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None, alias: str = 'os'):
    filtro = ''
    params = []
    prefixo = f'{alias}.' if alias else ''
    if periodo_inicio:
        filtro += f" AND date({prefixo}data_abertura) >= date(?)"
        params.append(periodo_inicio)
    if periodo_fim:
        filtro += f" AND date({prefixo}data_abertura) <= date(?)"
        params.append(periodo_fim)
    if equipamento_id not in (None, '', 'TODOS'):
        filtro += f" AND {prefixo}equipamento_id = ?"
        params.append(equipamento_id)
    return filtro, params


def dashboard_cards(periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None):
    cur = _cursor()

    filtro_periodo, params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, 'os')

    cur.execute(f"""
        SELECT
            SUM(CASE WHEN UPPER(os.status) = 'ABERTA' THEN 1 ELSE 0 END) AS abertas,
            SUM(CASE WHEN UPPER(os.status) = 'EM EXECUÇÃO' THEN 1 ELSE 0 END) AS em_execucao,
            SUM(COALESCE(os.custo_terceiro, 0)) AS custo_terceiro
        FROM ordens_servico os
        WHERE 1=1
        {filtro_periodo}
    """, params)
    row = dict(cur.fetchone() or {})

    cur.execute(f"""
        SELECT COALESCE(SUM(m.custo_total), 0) AS custo_materiais
        FROM os_materiais m
        JOIN ordens_servico os ON os.id = m.os_id
        WHERE 1=1
        {filtro_periodo}
    """, params)
    materiais = dict(cur.fetchone() or {})

    cur.execute(f"""
        SELECT COALESCE(SUM(ap.custo_hh_total), 0) AS custo_hh
        FROM os_apontamentos ap
        JOIN ordens_servico os ON os.id = ap.os_id
        WHERE 1=1
        {filtro_periodo}
    """, params)
    hh = dict(cur.fetchone() or {})

    paradas = listar_os_paradas(periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id)

    return {
        'abertas': int(row.get('abertas') or 0),
        'em_execucao': int(row.get('em_execucao') or 0),
        'os_paradas': len(paradas),
        'custo_total': round(
            float(materiais.get('custo_materiais') or 0)
            + float(hh.get('custo_hh') or 0)
            + float(row.get('custo_terceiro') or 0),
            2,
        ),
    }


def listar_os_sem_apontamento(periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None):
    cur = _cursor()

    filtro_periodo, params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, 'os')

    cur.execute(f"""
        SELECT
            os.id,
            os.numero,
            os.status,
            os.tipo_os,
            os.prioridade,
            os.data_abertura,
            eq.tag AS equipamento_tag,
            eq.descricao AS equipamento_descricao,
            cp.tag AS componente_tag,
            cp.descricao AS componente_descricao,
            (
                SELECT COUNT(*)
                FROM os_atividades a
                WHERE a.os_id = os.id
            ) AS total_atividades,
            (
                SELECT COUNT(*)
                FROM os_apontamentos ap
                WHERE ap.os_id = os.id
            ) AS total_apontamentos
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        LEFT JOIN ativos cp ON cp.id = os.componente_id
        WHERE UPPER(os.status) IN ('ABERTA', 'EM EXECUÇÃO')
          AND (
              SELECT COUNT(*)
              FROM os_apontamentos ap
              WHERE ap.os_id = os.id
          ) = 0
          {filtro_periodo}
        ORDER BY os.data_abertura ASC, os.numero ASC
    """, params)

    return [dict(row) for row in cur.fetchall()]


def listar_os_paradas(
    dias_sem_apontamento: int = 3,
    periodo_inicio: Optional[str] = None,
    periodo_fim: Optional[str] = None,
    equipamento_id: Optional[str] = None,
):
    cur = _cursor()

    filtro_periodo, extra_params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, 'os')
    params = [dias_sem_apontamento, *extra_params]

    if getattr(conn, 'backend', '') == 'postgres':
        sql_dias = "CAST((CURRENT_DATE - COALESCE(ua.ultima_data_apontamento, CAST(os.data_abertura AS date))) AS INTEGER)"
        sql_filtro_dias = "(CURRENT_DATE - ua.ultima_data_apontamento) >= ?"
    else:
        sql_dias = "CAST(julianday(date('now')) - julianday(COALESCE(ua.ultima_data_apontamento, os.data_abertura)) AS INTEGER)"
        sql_filtro_dias = "(julianday(date('now')) - julianday(ua.ultima_data_apontamento)) >= ?"

    cur.execute(f"""
        WITH ultimo_apontamento AS (
            SELECT
                ap.os_id,
                MAX(date(ap.data_apontamento)) AS ultima_data_apontamento
            FROM os_apontamentos ap
            GROUP BY ap.os_id
        )
        SELECT
            os.id,
            os.numero,
            os.status,
            os.tipo_os,
            os.prioridade,
            os.data_abertura,
            ua.ultima_data_apontamento,
            {sql_dias} AS dias_sem_apontamento,
            eq.tag AS equipamento_tag,
            eq.descricao AS equipamento_descricao,
            cp.tag AS componente_tag,
            cp.descricao AS componente_descricao,
            (
                SELECT COUNT(*)
                FROM os_atividades a
                WHERE a.os_id = os.id
                  AND UPPER(COALESCE(a.status, '')) IN ('ABERTA', 'EM EXECUÇÃO')
            ) AS atividades_pendentes
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        LEFT JOIN ativos cp ON cp.id = os.componente_id
        LEFT JOIN ultimo_apontamento ua ON ua.os_id = os.id
        WHERE UPPER(os.status) IN ('ABERTA', 'EM EXECUÇÃO')
          AND (
              SELECT COUNT(*)
              FROM os_atividades a
              WHERE a.os_id = os.id
                AND UPPER(COALESCE(a.status, '')) IN ('ABERTA', 'EM EXECUÇÃO')
          ) > 0
          AND (
              ua.ultima_data_apontamento IS NULL
              OR {sql_filtro_dias}
          )
          {filtro_periodo}
        ORDER BY dias_sem_apontamento DESC, os.data_abertura ASC
    """, params)

    return [dict(row) for row in cur.fetchall()]


def dashboard_os_mensal(periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None):
    cur = _cursor()
    filtro_periodo, params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, '')
    if getattr(conn, 'backend', '') == 'postgres':
        expr_ano_mes = "TO_CHAR(data_ref, 'YYYY-MM')"
    else:
        expr_ano_mes = "substr(data_ref, 1, 7)"
    cur.execute(f"""
        SELECT
            {expr_ano_mes} AS ano_mes,
            SUM(abertas) AS abertas,
            SUM(encerradas) AS encerradas
        FROM (
            SELECT date(data_abertura) AS data_ref, 1 AS abertas, 0 AS encerradas
            FROM ordens_servico
            WHERE data_abertura IS NOT NULL
            {filtro_periodo}

            UNION ALL

            SELECT date(data_encerramento) AS data_ref, 0 AS abertas, 1 AS encerradas
            FROM ordens_servico
            WHERE data_encerramento IS NOT NULL
            {filtro_periodo}
        ) x
        GROUP BY {expr_ano_mes}
        ORDER BY ano_mes
    """, tuple(params) + tuple(params))
    return [dict(row) for row in cur.fetchall()]


def dashboard_custo_mensal(periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None):
    cur = _cursor()
    filtro_periodo, params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, 'os')
    if getattr(conn, 'backend', '') == 'postgres':
        expr_ano_mes = "TO_CHAR(CAST(os.data_abertura AS date), 'YYYY-MM')"
        round_hh = "ROUND(SUM(COALESCE(ch.valor, 0))::numeric, 2)"
        round_mat = "ROUND(SUM(COALESCE(cm.valor, 0))::numeric, 2)"
        round_ter = "ROUND(SUM(COALESCE(os.custo_terceiro, 0))::numeric, 2)"
        round_total = "ROUND(SUM(COALESCE(ch.valor, 0) + COALESCE(cm.valor, 0) + COALESCE(os.custo_terceiro, 0))::numeric, 2)"
    else:
        expr_ano_mes = "substr(date(os.data_abertura), 1, 7)"
        round_hh = "ROUND(SUM(COALESCE(ch.valor, 0)), 2)"
        round_mat = "ROUND(SUM(COALESCE(cm.valor, 0)), 2)"
        round_ter = "ROUND(SUM(COALESCE(os.custo_terceiro, 0)), 2)"
        round_total = "ROUND(SUM(COALESCE(ch.valor, 0) + COALESCE(cm.valor, 0) + COALESCE(os.custo_terceiro, 0)), 2)"
    cur.execute(f"""
        WITH custo_hh AS (
            SELECT os_id, COALESCE(SUM(custo_hh_total), 0) AS valor
            FROM os_apontamentos
            GROUP BY os_id
        ),
        custo_mat AS (
            SELECT os_id, COALESCE(SUM(custo_total), 0) AS valor
            FROM os_materiais
            GROUP BY os_id
        )
        SELECT
            {expr_ano_mes} AS ano_mes,
            {round_hh} AS custo_hh,
            {round_mat} AS custo_materiais,
            {round_ter} AS custo_terceiro,
            {round_total} AS custo_total
        FROM ordens_servico os
        LEFT JOIN custo_hh ch ON ch.os_id = os.id
        LEFT JOIN custo_mat cm ON cm.os_id = os.id
        WHERE 1=1
        {filtro_periodo}
        GROUP BY {expr_ano_mes}
        ORDER BY ano_mes
    """, params)
    return [dict(row) for row in cur.fetchall()]


def dashboard_top_equipamentos_custo(limit: int = 10, periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None):
    cur = _cursor()
    filtro_periodo, params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, 'os')
    cur.execute(f"""
        WITH custo_hh AS (
            SELECT os_id, COALESCE(SUM(custo_hh_total), 0) AS valor
            FROM os_apontamentos
            GROUP BY os_id
        ),
        custo_mat AS (
            SELECT os_id, COALESCE(SUM(custo_total), 0) AS valor
            FROM os_materiais
            GROUP BY os_id
        )
        SELECT
            eq.id AS equipamento_id,
            eq.tag AS equipamento_tag,
            eq.descricao AS equipamento_descricao,
            COUNT(os.id) AS qtd_os,
            ROUND(SUM(COALESCE(ch.valor, 0) + COALESCE(cm.valor, 0) + COALESCE(os.custo_terceiro, 0)), 2) AS custo_total
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        LEFT JOIN custo_hh ch ON ch.os_id = os.id
        LEFT JOIN custo_mat cm ON cm.os_id = os.id
        WHERE 1=1
        {filtro_periodo}
        GROUP BY eq.id, eq.tag, eq.descricao
        ORDER BY custo_total DESC, qtd_os DESC, eq.tag ASC
        LIMIT ?
    """, tuple(params) + (int(limit),))
    return [dict(row) for row in cur.fetchall()]


def dashboard_top_equipamentos_os(limit: int = 10, periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None):
    cur = _cursor()
    filtro_periodo, params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, 'os')
    cur.execute(f"""
        SELECT
            eq.id AS equipamento_id,
            eq.tag AS equipamento_tag,
            eq.descricao AS equipamento_descricao,
            COUNT(os.id) AS qtd_os,
            SUM(CASE WHEN UPPER(os.status) = 'ABERTA' THEN 1 ELSE 0 END) AS abertas,
            SUM(CASE WHEN UPPER(os.status) = 'EM EXECUÇÃO' THEN 1 ELSE 0 END) AS em_execucao,
            SUM(CASE WHEN UPPER(os.status) = 'ENCERRADA' THEN 1 ELSE 0 END) AS encerradas
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        WHERE 1=1
        {filtro_periodo}
        GROUP BY eq.id, eq.tag, eq.descricao
        ORDER BY qtd_os DESC, eq.tag ASC
        LIMIT ?
    """, tuple(params) + (int(limit),))
    return [dict(row) for row in cur.fetchall()]


def dashboard_retrabalho(periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None, limit: int = 20):
    cur = _cursor()
    filtro_periodo, params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, 'os')
    cur.execute(f"""
        SELECT
            os.equipamento_id,
            os.componente_id,
            eq.tag AS equipamento_tag,
            eq.descricao AS equipamento_descricao,
            cp.tag AS componente_tag,
            cp.descricao AS componente_descricao,
            COUNT(os.id) AS qtd_os_encerradas,
            MAX(date(os.data_encerramento)) AS ultima_data_encerramento
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        LEFT JOIN ativos cp ON cp.id = os.componente_id
        WHERE UPPER(COALESCE(os.status, '')) = 'ENCERRADA'
          AND os.data_encerramento IS NOT NULL
          {filtro_periodo}
        GROUP BY os.equipamento_id, os.componente_id, eq.tag, eq.descricao, cp.tag, cp.descricao
        HAVING COUNT(os.id) > 1
        ORDER BY qtd_os_encerradas DESC, eq.tag ASC, cp.tag ASC
        LIMIT ?
    """, tuple(params) + (int(limit),))
    linhas = []
    total_retrabalho = 0
    for row in cur.fetchall():
        item = dict(row)
        qtd_os = int(item.get('qtd_os_encerradas') or 0)
        item['qtd_os_encerradas'] = qtd_os
        item['qtd_reincidencias'] = max(qtd_os - 1, 0)
        total_retrabalho += item['qtd_reincidencias']
        linhas.append(item)

    cur.execute(f"""
        SELECT COUNT(*) AS total_encerradas
        FROM ordens_servico os
        WHERE UPPER(COALESCE(os.status, '')) = 'ENCERRADA'
          AND os.data_encerramento IS NOT NULL
          {filtro_periodo}
    """, params)
    total_encerradas = int(dict(cur.fetchone() or {}).get('total_encerradas') or 0)

    percentual = round((total_retrabalho / total_encerradas) * 100, 2) if total_encerradas else 0.0
    return {
        'total_retrabalho': total_retrabalho,
        'itens_reincidentes': len(linhas),
        'total_encerradas': total_encerradas,
        'percentual_os_retrabalho': percentual,
        'linhas': linhas,
    }


# =========================
# PERFORMANCE / FINAL OVERRIDES
# =========================

def _ensure_performance_indexes():
    cur = _cursor()
    comandos = [
        "CREATE INDEX IF NOT EXISTS idx_ativos_parent_id ON ativos(parent_id)",
        "CREATE INDEX IF NOT EXISTS idx_ativos_tipo ON ativos(tipo)",
        "CREATE INDEX IF NOT EXISTS idx_ativos_tag ON ativos(tag)",
        "CREATE INDEX IF NOT EXISTS idx_ativos_parent_tipo ON ativos(parent_id, tipo)",
        "CREATE INDEX IF NOT EXISTS idx_os_status ON ordens_servico(status)",
        "CREATE INDEX IF NOT EXISTS idx_os_data_abertura ON ordens_servico(data_abertura)",
        "CREATE INDEX IF NOT EXISTS idx_os_equipamento_id ON ordens_servico(equipamento_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_numero ON ordens_servico(numero)",
        "CREATE INDEX IF NOT EXISTS idx_os_componente_id ON ordens_servico(componente_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_atividades_os_id ON os_atividades(os_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_apontamentos_os_id ON os_apontamentos(os_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_apontamentos_atividade_id ON os_apontamentos(atividade_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_materiais_os_id ON os_materiais(os_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_materiais_atividade_id ON os_materiais(atividade_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_anexos_os_id ON os_anexos(os_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_status_data_abertura ON ordens_servico(status, data_abertura DESC)",
        "CREATE INDEX IF NOT EXISTS idx_os_equipamento_data_abertura ON ordens_servico(equipamento_id, data_abertura DESC)",
        "CREATE INDEX IF NOT EXISTS idx_os_componente_data_abertura ON ordens_servico(componente_id, data_abertura DESC)",
        "CREATE INDEX IF NOT EXISTS idx_os_atividades_os_status ON os_atividades(os_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_os_apontamentos_os_data ON os_apontamentos(os_id, data_apontamento)",
        "CREATE INDEX IF NOT EXISTS idx_os_materiais_os_atividade ON os_materiais(os_id, atividade_id)",
    ]
    for sql in comandos:
        try:
            cur.execute(sql)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    conn.commit()


_ensure_performance_indexes()


def _sanear_campos_por_tipo(
    tipo: str,
    criticidade: Optional[int],
    fabricante: Optional[str],
    modelo: Optional[str],
    numero_serie: Optional[str],
    ano_fabricacao: Optional[int],
    ativo: bool,
    pecas_ativas: bool,
    pecas_json: Any,
):
    tipo = _upper(tipo)
    fabricante = _upper(fabricante)
    modelo = _upper(modelo)
    numero_serie = _upper(numero_serie)
    pecas_json = _dump_pecas(pecas_json)

    if tipo == 'LOCAL':
        criticidade = None
        fabricante = ''
        modelo = ''
        numero_serie = ''
        ano_fabricacao = None
        ativo = 1
        pecas_ativas = 0
        pecas_json = '[]'
    elif tipo == 'EQUIPAMENTO':
        ativo = 1 if ativo else 0
        pecas_ativas = 1 if pecas_ativas else 0
    elif tipo == 'COMPONENTE':
        ano_fabricacao = None
        ativo = 1
        pecas_ativas = 1 if pecas_ativas else 0
    else:
        raise ValueError('Tipo inválido.')

    if criticidade in ('', None):
        criticidade = None
    else:
        criticidade = int(criticidade)

    if ano_fabricacao in ('', None):
        ano_fabricacao = None
    else:
        ano_fabricacao = int(ano_fabricacao)

    return criticidade, fabricante, modelo, numero_serie, ano_fabricacao, ativo, pecas_ativas, pecas_json


def listar_alvos_os():
    cur = _cursor()
    cur.execute(
        """
        SELECT
            a.id,
            a.tag,
            a.descricao,
            a.tipo,
            a.parent_id,
            CASE
                WHEN a.tipo = 'COMPONENTE' THEN COALESCE(p.tag, '')
                ELSE a.tag
            END AS equipamento_tag
        FROM ativos a
        LEFT JOIN ativos p ON p.id = a.parent_id
        WHERE a.tipo IN ('EQUIPAMENTO', 'COMPONENTE')
        ORDER BY a.tag
        """
    )
    return [dict(r) for r in cur.fetchall()]


def listar_os(busca: Optional[str] = None, status: Optional[str] = None):
    """Lista leve para a tela de OS.

    Aqui evitamos somatórios e subconsultas pesadas, porque a sidebar/lista
    só precisa de dados básicos. Os totais ficam para get_os_detalhe().
    """
    busca = _upper(busca)
    status = _upper(status)
    cur = _cursor()
    where = []
    params = []
    if status:
        where.append("UPPER(COALESCE(os.status, '')) = ?")
        params.append(status)
    if busca:
        like = f"%{busca}%"
        where.append(
            "(" + " OR ".join([
                "UPPER(COALESCE(os.numero, '')) LIKE ?",
                "UPPER(COALESCE(os.descricao, '')) LIKE ?",
                "UPPER(COALESCE(os.tipo_os, '')) LIKE ?",
                "UPPER(COALESCE(os.prioridade, '')) LIKE ?",
                "UPPER(COALESCE(os.status, '')) LIKE ?",
                "UPPER(COALESCE(eq.tag, '')) LIKE ?",
                "UPPER(COALESCE(eq.descricao, '')) LIKE ?",
                "UPPER(COALESCE(cp.tag, '')) LIKE ?",
                "UPPER(COALESCE(cp.descricao, '')) LIKE ?"
            ]) + ")"
        )
        params.extend([like] * 9)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    cur.execute(
        f"""
        SELECT
            os.id,
            os.numero,
            os.status,
            os.tipo_os,
            os.prioridade,
            os.descricao,
            os.data_abertura,
            os.data_encerramento,
            os.equipamento_id,
            os.componente_id,
            eq.tag AS equipamento_tag,
            eq.descricao AS equipamento_descricao,
            cp.tag AS componente_tag,
            cp.descricao AS componente_descricao
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        LEFT JOIN ativos cp ON cp.id = os.componente_id
        {where_sql}
        ORDER BY
            CASE WHEN os.data_abertura IS NULL OR os.data_abertura = '' THEN 1 ELSE 0 END,
            os.data_abertura DESC,
            os.created_at DESC,
            os.numero DESC
        """,
        tuple(params),
    )
    return [dict(r) for r in cur.fetchall()]


def get_os(os_id: str):
    cur = _cursor()
    cur.execute(
        """
        SELECT
            os.*,
            eq.id AS equipamento_join_id,
            eq.tag AS equipamento_tag,
            eq.tag_base AS equipamento_tag_base,
            eq.descricao AS equipamento_descricao,
            eq.tipo AS equipamento_tipo,
            eq.parent_id AS equipamento_parent_id,
            eq.criticidade AS equipamento_criticidade,
            eq.fabricante AS equipamento_fabricante,
            eq.modelo AS equipamento_modelo,
            eq.numero_serie AS equipamento_numero_serie,
            eq.ano_fabricacao AS equipamento_ano_fabricacao,
            eq.ativo AS equipamento_ativo,
            eq.pecas_ativas AS equipamento_pecas_ativas,
            eq.pecas_json AS equipamento_pecas_json,
            cp.id AS componente_join_id,
            cp.tag AS componente_tag,
            cp.tag_base AS componente_tag_base,
            cp.descricao AS componente_descricao,
            cp.tipo AS componente_tipo,
            cp.parent_id AS componente_parent_id,
            cp.criticidade AS componente_criticidade,
            cp.fabricante AS componente_fabricante,
            cp.modelo AS componente_modelo,
            cp.numero_serie AS componente_numero_serie,
            cp.ano_fabricacao AS componente_ano_fabricacao,
            cp.ativo AS componente_ativo,
            cp.pecas_ativas AS componente_pecas_ativas,
            cp.pecas_json AS componente_pecas_json,
            COALESCE(atv.total_atividades, 0) AS total_atividades,
            COALESCE(mat.total_materiais, 0) AS total_materiais,
            COALESCE(ap.total_apontamentos, 0) AS total_apontamentos
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        LEFT JOIN ativos cp ON cp.id = os.componente_id
        LEFT JOIN (SELECT os_id, COUNT(*) AS total_atividades FROM os_atividades GROUP BY os_id) atv ON atv.os_id = os.id
        LEFT JOIN (SELECT os_id, COUNT(*) AS total_materiais FROM os_materiais GROUP BY os_id) mat ON mat.os_id = os.id
        LEFT JOIN (SELECT os_id, COUNT(*) AS total_apontamentos FROM os_apontamentos GROUP BY os_id) ap ON ap.os_id = os.id
        WHERE os.id = ?
        LIMIT 1
        """,
        (os_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    item = dict(row)
    equipamento = {
        'id': item.get('equipamento_join_id'),
        'tag': item.get('equipamento_tag'),
        'tag_base': item.get('equipamento_tag_base'),
        'descricao': item.get('equipamento_descricao'),
        'tipo': item.get('equipamento_tipo'),
        'parent_id': item.get('equipamento_parent_id'),
        'criticidade': item.get('equipamento_criticidade'),
        'fabricante': item.get('equipamento_fabricante'),
        'modelo': item.get('equipamento_modelo'),
        'numero_serie': item.get('equipamento_numero_serie'),
        'ano_fabricacao': item.get('equipamento_ano_fabricacao'),
        'ativo': bool(item.get('equipamento_ativo', 1)),
        'pecas_ativas': bool(item.get('equipamento_pecas_ativas', 0)),
        'pecas_json': _parse_pecas(item.get('equipamento_pecas_json')),
    }
    componente = None
    if item.get('componente_join_id'):
        componente = {
            'id': item.get('componente_join_id'),
            'tag': item.get('componente_tag'),
            'tag_base': item.get('componente_tag_base'),
            'descricao': item.get('componente_descricao'),
            'tipo': item.get('componente_tipo'),
            'parent_id': item.get('componente_parent_id'),
            'criticidade': item.get('componente_criticidade'),
            'fabricante': item.get('componente_fabricante'),
            'modelo': item.get('componente_modelo'),
            'numero_serie': item.get('componente_numero_serie'),
            'ano_fabricacao': item.get('componente_ano_fabricacao'),
            'ativo': bool(item.get('componente_ativo', 1)),
            'pecas_ativas': bool(item.get('componente_pecas_ativas', 0)),
            'pecas_json': _parse_pecas(item.get('componente_pecas_json')),
        }
    item['equipamento'] = equipamento
    item['componente'] = componente
    item['anexos'] = listar_os_anexos(os_id)
    return item


def get_os_detalhe(os_id: str):
    item = get_os(os_id)
    if not item:
        return None
    atividades = listar_os_atividades(os_id)
    materiais = listar_os_materiais(os_id)
    apontamentos = listar_os_apontamentos(os_id)
    anexos = listar_os_anexos(os_id)
    totais = calcular_totais_os(os_id)
    return {
        'item': item,
        'atividades': atividades,
        'materiais': materiais,
        'apontamentos': apontamentos,
        'anexos': anexos,
        'totais': totais,
    }


def listar_os_por_ativo(ativo_id: str, incluir_componentes: bool = False):
    cur = _cursor()
    if incluir_componentes:
        cur.execute(
            """
            SELECT os.*, eq.tag AS equipamento_tag, eq.descricao AS equipamento_descricao,
                   cp.tag AS componente_tag, cp.descricao AS componente_descricao
            FROM ordens_servico os
            JOIN ativos eq ON eq.id = os.equipamento_id
            LEFT JOIN ativos cp ON cp.id = os.componente_id
            WHERE os.equipamento_id = ? OR os.componente_id = ?
            ORDER BY CASE WHEN os.data_abertura IS NULL OR os.data_abertura = '' THEN 1 ELSE 0 END, os.data_abertura DESC, os.created_at DESC, os.numero DESC
            """,
            (ativo_id, ativo_id),
        )
    else:
        cur.execute(
            """
            SELECT os.*, eq.tag AS equipamento_tag, eq.descricao AS equipamento_descricao,
                   cp.tag AS componente_tag, cp.descricao AS componente_descricao
            FROM ordens_servico os
            JOIN ativos eq ON eq.id = os.equipamento_id
            LEFT JOIN ativos cp ON cp.id = os.componente_id
            WHERE os.equipamento_id = ?
            ORDER BY CASE WHEN os.data_abertura IS NULL OR os.data_abertura = '' THEN 1 ELSE 0 END, os.data_abertura DESC, os.created_at DESC, os.numero DESC
            """,
            (ativo_id,),
        )
    return [dict(r) for r in cur.fetchall()]


# ===== Performance overrides (2026-03-31) =====

def _ensure_perf_indexes_extra():
    comandos = [
        "CREATE INDEX IF NOT EXISTS idx_ativos_descricao ON ativos(descricao)",
        "CREATE INDEX IF NOT EXISTS idx_ativos_tag_tipo ON ativos(tag, tipo)",
        "CREATE INDEX IF NOT EXISTS idx_ativos_parent_tag ON ativos(parent_id, tag)",
        "CREATE INDEX IF NOT EXISTS idx_os_equipamento_status ON ordens_servico(equipamento_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_os_componente_status ON ordens_servico(componente_id, status)",
    ]
    cur = _cursor()
    for sql in comandos:
        try:
            cur.execute(sql)
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass


_ensure_perf_indexes_extra()


def listar_ativos(busca: Optional[str] = None):
    busca = _upper(busca)
    cur = _cursor()
    where = []
    params = []
    if busca:
        like = f"%{busca}%"
        where.append(
            "(" + " OR ".join([
                "UPPER(COALESCE(a.tipo, '')) LIKE ?",
                "UPPER(COALESCE(a.tag, '')) LIKE ?",
                "UPPER(COALESCE(a.tag_base, '')) LIKE ?",
                "UPPER(COALESCE(a.descricao, '')) LIKE ?",
                "UPPER(COALESCE(a.observacoes, '')) LIKE ?",
                "UPPER(COALESCE(a.fabricante, '')) LIKE ?",
                "UPPER(COALESCE(a.modelo, '')) LIKE ?",
                "UPPER(COALESCE(a.numero_serie, '')) LIKE ?",
                "UPPER(COALESCE(p.tag, '')) LIKE ?",
                "UPPER(COALESCE(p.descricao, '')) LIKE ?",
            ]) + ")"
        )
        params.extend([like] * 10)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    cur.execute(
        f"""
        SELECT
            a.*,
            p.tag AS parent_tag,
            p.descricao AS parent_descricao,
            CASE
                WHEN UPPER(COALESCE(a.tipo, '')) = 'COMPONENTE' THEN COALESCE(osc.qtd_abertas, 0)
                WHEN UPPER(COALESCE(a.tipo, '')) = 'EQUIPAMENTO' THEN COALESCE(ose.qtd_abertas, 0)
                ELSE 0
            END AS qtd_os_abertas
        FROM ativos a
        LEFT JOIN ativos p ON p.id = a.parent_id
        LEFT JOIN (
            SELECT componente_id AS ativo_id, COUNT(*) AS qtd_abertas
            FROM ordens_servico
            WHERE componente_id IS NOT NULL
              AND UPPER(COALESCE(status, '')) IN ('ABERTA', 'EM EXECUÇÃO')
            GROUP BY componente_id
        ) osc ON osc.ativo_id = a.id
        LEFT JOIN (
            SELECT equipamento_id AS ativo_id, COUNT(*) AS qtd_abertas
            FROM ordens_servico
            WHERE equipamento_id IS NOT NULL
              AND UPPER(COALESCE(status, '')) IN ('ABERTA', 'EM EXECUÇÃO')
            GROUP BY equipamento_id
        ) ose ON ose.ativo_id = a.id
        {where_sql}
        ORDER BY a.tag
        """,
        tuple(params),
    )
    resultado = []
    for row in cur.fetchall():
        item = dict(row)
        item['ativo'] = bool(item.get('ativo', 1))
        item['pecas_json'] = _parse_pecas(item.get('pecas_json'))
        item['pecas_ativas'] = bool(item.get('pecas_ativas'))
        item['qtd_os_abertas'] = int(item.get('qtd_os_abertas') or 0)
        item['tem_os_aberta'] = item['qtd_os_abertas'] > 0
        resultado.append(item)
    return resultado


def get_ativos(busca: Optional[str] = None):
    return listar_ativos(busca)


def get_os(os_id: str):
    cur = _cursor()
    cur.execute(
        """
        SELECT
            os.*,
            eq.id AS equipamento_join_id,
            eq.tag AS equipamento_tag,
            eq.tag_base AS equipamento_tag_base,
            eq.descricao AS equipamento_descricao,
            eq.tipo AS equipamento_tipo,
            eq.parent_id AS equipamento_parent_id,
            eq.criticidade AS equipamento_criticidade,
            eq.fabricante AS equipamento_fabricante,
            eq.modelo AS equipamento_modelo,
            eq.numero_serie AS equipamento_numero_serie,
            eq.ano_fabricacao AS equipamento_ano_fabricacao,
            eq.ativo AS equipamento_ativo,
            eq.pecas_ativas AS equipamento_pecas_ativas,
            eq.pecas_json AS equipamento_pecas_json,
            cp.id AS componente_join_id,
            cp.tag AS componente_tag,
            cp.tag_base AS componente_tag_base,
            cp.descricao AS componente_descricao,
            cp.tipo AS componente_tipo,
            cp.parent_id AS componente_parent_id,
            cp.criticidade AS componente_criticidade,
            cp.fabricante AS componente_fabricante,
            cp.modelo AS componente_modelo,
            cp.numero_serie AS componente_numero_serie,
            cp.ano_fabricacao AS componente_ano_fabricacao,
            cp.ativo AS componente_ativo,
            cp.pecas_ativas AS componente_pecas_ativas,
            cp.pecas_json AS componente_pecas_json,
            COALESCE(atv.total_atividades, 0) AS total_atividades,
            COALESCE(mat.total_materiais, 0) AS total_materiais,
            COALESCE(ap.total_apontamentos, 0) AS total_apontamentos
        FROM ordens_servico os
        JOIN ativos eq ON eq.id = os.equipamento_id
        LEFT JOIN ativos cp ON cp.id = os.componente_id
        LEFT JOIN (SELECT os_id, COUNT(*) AS total_atividades FROM os_atividades GROUP BY os_id) atv ON atv.os_id = os.id
        LEFT JOIN (SELECT os_id, COUNT(*) AS total_materiais FROM os_materiais GROUP BY os_id) mat ON mat.os_id = os.id
        LEFT JOIN (SELECT os_id, COUNT(*) AS total_apontamentos FROM os_apontamentos GROUP BY os_id) ap ON ap.os_id = os.id
        WHERE os.id = ?
        LIMIT 1
        """,
        (os_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    item = dict(row)
    equipamento = {
        'id': item.get('equipamento_join_id'),
        'tag': item.get('equipamento_tag'),
        'tag_base': item.get('equipamento_tag_base'),
        'descricao': item.get('equipamento_descricao'),
        'tipo': item.get('equipamento_tipo'),
        'parent_id': item.get('equipamento_parent_id'),
        'criticidade': item.get('equipamento_criticidade'),
        'fabricante': item.get('equipamento_fabricante'),
        'modelo': item.get('equipamento_modelo'),
        'numero_serie': item.get('equipamento_numero_serie'),
        'ano_fabricacao': item.get('equipamento_ano_fabricacao'),
        'ativo': bool(item.get('equipamento_ativo', 1)),
        'pecas_ativas': bool(item.get('equipamento_pecas_ativas', 0)),
        'pecas_json': _parse_pecas(item.get('equipamento_pecas_json')),
    }
    componente = None
    if item.get('componente_join_id'):
        componente = {
            'id': item.get('componente_join_id'),
            'tag': item.get('componente_tag'),
            'tag_base': item.get('componente_tag_base'),
            'descricao': item.get('componente_descricao'),
            'tipo': item.get('componente_tipo'),
            'parent_id': item.get('componente_parent_id'),
            'criticidade': item.get('componente_criticidade'),
            'fabricante': item.get('componente_fabricante'),
            'modelo': item.get('componente_modelo'),
            'numero_serie': item.get('componente_numero_serie'),
            'ano_fabricacao': item.get('componente_ano_fabricacao'),
            'ativo': bool(item.get('componente_ativo', 1)),
            'pecas_ativas': bool(item.get('componente_pecas_ativas', 0)),
            'pecas_json': _parse_pecas(item.get('componente_pecas_json')),
        }
    item['equipamento'] = equipamento
    item['componente'] = componente
    return item


def get_os_detalhe(os_id: str):
    item = get_os(os_id)
    if not item:
        return None
    atividades = listar_os_atividades(os_id)
    materiais = listar_os_materiais(os_id)
    apontamentos = listar_os_apontamentos(os_id)
    anexos = listar_os_anexos(os_id)
    totais = calcular_totais_os(os_id)
    item['anexos'] = anexos
    return {
        'item': item,
        'atividades': atividades,
        'materiais': materiais,
        'apontamentos': apontamentos,
        'anexos': anexos,
        'totais': totais,
    }

# ===== Segurança, e-mail e auditoria (2026-04-21) =====
from nicegui import app
import base64
import hashlib
import hmac
import secrets
import smtplib
from email.message import EmailMessage
from config.settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_USE_TLS,
    SMTP_FROM_EMAIL, SMTP_FROM_NAME, APP_BASE_URL,
)


def _hash_password(password: str, iterations: int = 200_000) -> str:
    password = str(password or '')
    if not password:
        raise ValueError('Senha inválida.')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(iterations))
    return f"pbkdf2_sha256${int(iterations)}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _verify_password(password: str, stored: str) -> bool:
    password = str(password or '')
    stored = str(stored or '')
    if not stored:
        return False
    if stored.startswith('pbkdf2_sha256$'):
        try:
            _algo, raw_iter, raw_salt, raw_hash = stored.split('$', 3)
            iterations = int(raw_iter)
            salt = base64.b64decode(raw_salt.encode())
            expected = base64.b64decode(raw_hash.encode())
            current = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
            return hmac.compare_digest(current, expected)
        except Exception:
            return False
    return hmac.compare_digest(password, stored)


def _needs_password_upgrade(stored: str) -> bool:
    return not str(stored or '').startswith('pbkdf2_sha256$')


def _gerar_senha_temporaria(tamanho: int = 14) -> str:
    # Evita caracteres ambíguos e símbolos que costumam gerar cópia incorreta do e-mail.
    alfabeto = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    return ''.join(secrets.choice(alfabeto) for _ in range(max(12, int(tamanho))))


def _smtp_configurado() -> bool:
    return bool(SMTP_HOST and SMTP_PORT and SMTP_FROM_EMAIL)


def _enviar_email(destinatario: str, assunto: str, corpo_texto: str) -> bool:
    destinatario = str(destinatario or '').strip()
    if not destinatario or not _smtp_configurado():
        return False
    msg = EmailMessage()
    msg['Subject'] = assunto
    msg['From'] = f'{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>' if SMTP_FROM_NAME else SMTP_FROM_EMAIL
    msg['To'] = destinatario
    msg.set_content(corpo_texto)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    return True


def enviar_credenciais_usuario_email(nome: str, email: str, username: str, senha_temporaria: str) -> bool:
    nome = str(nome or '').strip() or 'Usuário'
    url = APP_BASE_URL or 'URL_DO_APP_NAO_CONFIGURADA'
    corpo = f'''Olá, {nome}.

Seu acesso ao Maintenance APP foi criado/atualizado.

Usuário: {username}
Senha temporária: {senha_temporaria}

No primeiro acesso, altere a senha imediatamente.
Link de acesso: {url}
'''
    return _enviar_email(email, 'Credenciais de acesso - Maintenance APP', corpo)


def _ensure_security_audit_schema():
    cur = _cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            usuario_id TEXT NULL,
            usuario_username TEXT NULL,
            usuario_nome TEXT NULL,
            acao TEXT NOT NULL,
            entidade TEXT NOT NULL,
            registro_id TEXT NULL,
            detalhes_json TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cols = _get_columns('usuarios')
    except Exception:
        cols = []
    alteracoes = {
        'nome': "ALTER TABLE usuarios ADD COLUMN nome TEXT NULL",
        'email': "ALTER TABLE usuarios ADD COLUMN email TEXT NULL",
        'pode_ver_logs': "ALTER TABLE usuarios ADD COLUMN pode_ver_logs INTEGER NOT NULL DEFAULT 0",
    }
    for coluna, ddl in alteracoes.items():
        if coluna not in cols:
            try:
                cur.execute(ddl)
            except Exception:
                pass
    try:
        cur.execute("UPDATE usuarios SET nome = COALESCE(nome, username) WHERE COALESCE(nome, '') = ''")
    except Exception:
        pass
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC)")
    except Exception:
        pass
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_usuario_username ON audit_logs(usuario_username)")
    except Exception:
        pass
    conn.commit()


_ensure_security_audit_schema()


# ===== CONTROLE GRANULAR DE ACESSO (2026-04-21) =====

PERFIS_ACESSO_PADRAO = ['ADMIN', 'GESTOR', 'PLANEJADOR', 'EXECUTOR', 'VISUALIZACAO']
MODULOS_SISTEMA = ['HOME', 'ARVORE', 'EQUIPAMENTOS', 'OS', 'EQUIPES', 'FUNCIONARIOS', 'USUARIOS', 'DASHBOARD', 'LOGS']
CAMPOS_PERMISSAO_SISTEMA = [
    'ver_menu',
    'abrir_tela',
    'criar',
    'editar',
    'excluir',
    'exportar',
    'aprovar_liberar',
    'ver_logs',
    'gerenciar_usuarios',
    'gerenciar_permissoes',
]


def _permissoes_padrao_por_perfil() -> dict:
    tudo = {k: 1 for k in CAMPOS_PERMISSAO_SISTEMA}
    zero = {k: 0 for k in CAMPOS_PERMISSAO_SISTEMA}

    admin = {mod: dict(tudo) for mod in MODULOS_SISTEMA}

    gestor = {mod: dict(zero) for mod in MODULOS_SISTEMA}
    for mod in ['HOME', 'ARVORE', 'EQUIPAMENTOS', 'OS', 'EQUIPES', 'FUNCIONARIOS', 'DASHBOARD']:
        gestor[mod].update({'ver_menu': 1, 'abrir_tela': 1, 'criar': 1, 'editar': 1, 'exportar': 1})
    gestor['OS'].update({'aprovar_liberar': 1})
    gestor['LOGS'].update({'ver_menu': 1, 'abrir_tela': 1, 'ver_logs': 1, 'exportar': 1})

    planejador = {mod: dict(zero) for mod in MODULOS_SISTEMA}
    for mod in ['HOME', 'ARVORE', 'EQUIPAMENTOS', 'OS', 'DASHBOARD']:
        planejador[mod].update({'ver_menu': 1, 'abrir_tela': 1})
    planejador['EQUIPAMENTOS'].update({'criar': 1, 'editar': 1, 'exportar': 1})
    planejador['OS'].update({'criar': 1, 'editar': 1, 'exportar': 1})

    executor = {mod: dict(zero) for mod in MODULOS_SISTEMA}
    for mod in ['HOME', 'ARVORE', 'OS']:
        executor[mod].update({'ver_menu': 1, 'abrir_tela': 1})
    executor['OS'].update({'criar': 1, 'editar': 1})

    visual = {mod: dict(zero) for mod in MODULOS_SISTEMA}
    for mod in ['HOME', 'ARVORE', 'OS', 'DASHBOARD']:
        visual[mod].update({'ver_menu': 1, 'abrir_tela': 1})

    return {
        'ADMIN': admin,
        'GESTOR': gestor,
        'PLANEJADOR': planejador,
        'EXECUTOR': executor,
        'VISUALIZACAO': visual,
    }


def _normalize_perfil_acesso(nome: str) -> str:
    valor = str(nome or '').strip().upper()
    mapa = {
        'GERENCIA': 'GESTOR',
        'COMPLETO': 'GESTOR',
        'TECNICO': 'EXECUTOR',
    }
    valor = mapa.get(valor, valor)
    return valor if valor in PERFIS_ACESSO_PADRAO else 'VISUALIZACAO'


def _ensure_access_control_schema():
    cur = _cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS perfis_acesso (
            nome TEXT PRIMARY KEY,
            descricao TEXT NULL,
            ativo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS perfil_permissoes (
            id TEXT PRIMARY KEY,
            perfil_nome TEXT NOT NULL,
            modulo TEXT NOT NULL,
            ver_menu INTEGER NOT NULL DEFAULT 0,
            abrir_tela INTEGER NOT NULL DEFAULT 0,
            criar INTEGER NOT NULL DEFAULT 0,
            editar INTEGER NOT NULL DEFAULT 0,
            excluir INTEGER NOT NULL DEFAULT 0,
            exportar INTEGER NOT NULL DEFAULT 0,
            aprovar_liberar INTEGER NOT NULL DEFAULT 0,
            ver_logs INTEGER NOT NULL DEFAULT 0,
            gerenciar_usuarios INTEGER NOT NULL DEFAULT 0,
            gerenciar_permissoes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(perfil_nome, modulo)
        )
    """)
    defaults = _permissoes_padrao_por_perfil()
    descricoes = {
        'ADMIN': 'Administrador do sistema',
        'GESTOR': 'Gestor com amplo acesso operacional',
        'PLANEJADOR': 'Perfil focado em planejamento',
        'EXECUTOR': 'Perfil focado na execução',
        'VISUALIZACAO': 'Perfil de consulta',
    }
    for perfil in PERFIS_ACESSO_PADRAO:
        try:
            cur.execute(
                "INSERT INTO perfis_acesso (nome, descricao, ativo, created_at, updated_at) VALUES (?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT (nome) DO NOTHING",
                (perfil, descricoes.get(perfil)),
            )
        except Exception:
            pass
        for modulo in MODULOS_SISTEMA:
            valores = defaults[perfil][modulo]
            try:
                cur.execute(
                    f"""
                    INSERT INTO perfil_permissoes (
                        id, perfil_nome, modulo, {", ".join(CAMPOS_PERMISSAO_SISTEMA)}, created_at, updated_at
                    )
                    VALUES (
                        ?, ?, ?, {", ".join(["?"] * len(CAMPOS_PERMISSAO_SISTEMA))}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (perfil_nome, modulo) DO NOTHING
                    """,
                    (str(uuid.uuid4()), perfil, modulo, *[int(valores[c]) for c in CAMPOS_PERMISSAO_SISTEMA]),
                )
            except Exception:
                pass
    try:
        cur.execute("UPDATE usuarios SET nivel_acesso = 'GESTOR' WHERE upper(nivel_acesso) IN ('GERENCIA', 'COMPLETO')")
    except Exception:
        pass
    try:
        cur.execute("UPDATE usuarios SET nivel_acesso = 'EXECUTOR' WHERE upper(nivel_acesso) = 'TECNICO'")
    except Exception:
        pass
    try:
        cur.execute("UPDATE usuarios SET nivel_acesso = 'ADMIN' WHERE lower(username) = 'admin'")
    except Exception:
        pass
    conn.commit()


def listar_perfis_acesso():
    cur = _cursor()
    cur.execute("""
        SELECT nome, descricao, ativo
        FROM perfis_acesso
        WHERE ativo = 1
        ORDER BY CASE nome
            WHEN 'ADMIN' THEN 1
            WHEN 'GESTOR' THEN 2
            WHEN 'PLANEJADOR' THEN 3
            WHEN 'EXECUTOR' THEN 4
            ELSE 5
        END, nome
    """)
    return [dict(r) for r in cur.fetchall()]


def listar_permissoes_perfil(perfil_nome: str):
    perfil_nome = _normalize_perfil_acesso(perfil_nome)
    cur = _cursor()
    cur.execute(
        f"SELECT perfil_nome, modulo, {', '.join(CAMPOS_PERMISSAO_SISTEMA)} FROM perfil_permissoes WHERE perfil_nome = ? ORDER BY modulo",
        (perfil_nome,),
    )
    return [dict(r) for r in cur.fetchall()]


def obter_mapa_permissoes_perfil(perfil_nome: str) -> dict:
    linhas = listar_permissoes_perfil(perfil_nome)
    mapa = {}
    for item in linhas:
        modulo = str(item.get('modulo') or '').upper()
        mapa[modulo] = {campo: bool(item.get(campo, 0)) for campo in CAMPOS_PERMISSAO_SISTEMA}
    return mapa


def atualizar_permissoes_perfil(perfil_nome: str, modulo: str, permissoes: dict):
    perfil_nome = _normalize_perfil_acesso(perfil_nome)
    modulo = str(modulo or '').strip().upper()
    if perfil_nome == 'ADMIN':
        raise ValueError('O perfil ADMIN é fixo e não pode ser alterado.')
    if modulo not in MODULOS_SISTEMA:
        raise ValueError('Módulo inválido.')
    payload = {campo: 1 if bool((permissoes or {}).get(campo)) else 0 for campo in CAMPOS_PERMISSAO_SISTEMA}
    cur = _cursor()
    cur.execute(
        f"""
        UPDATE perfil_permissoes
        SET {", ".join([f"{campo} = ?" for campo in CAMPOS_PERMISSAO_SISTEMA])},
            updated_at = CURRENT_TIMESTAMP
        WHERE perfil_nome = ? AND modulo = ?
        """,
        (*[payload[c] for c in CAMPOS_PERMISSAO_SISTEMA], perfil_nome, modulo),
    )
    conn.commit()
    try:
        registrar_log_acao('ATUALIZAR', 'USUARIO', None, {
            'tipo': 'PERFIL_PERMISSOES',
            'perfil_nome': perfil_nome,
            'modulo': modulo,
            'permissoes': payload,
        })
    except Exception:
        pass
    return {'perfil_nome': perfil_nome, 'modulo': modulo, 'permissoes': payload}


_ensure_access_control_schema()



def registrar_log_acao(acao: str, entidade: str, registro_id: str = None, detalhes: dict | None = None, usuario_id: str = None):
    cur = _cursor()
    usuario = None
    if usuario_id:
        try:
            cur.execute("SELECT id, username, nome FROM usuarios WHERE id = ?", (usuario_id,))
            row = cur.fetchone()
            usuario = dict(row) if row else None
        except Exception:
            usuario = None
    elif 'app' in globals():
        try:
            sess = app.storage.user
            if sess.get('usuario_id'):
                usuario = {'id': sess.get('usuario_id'), 'username': sess.get('username'), 'nome': sess.get('name')}
        except Exception:
            usuario = None
    log_id = str(uuid.uuid4())
    payload = json.dumps(detalhes or {}, ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO audit_logs (id, usuario_id, usuario_username, usuario_nome, acao, entidade, registro_id, detalhes_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            log_id,
            usuario.get('id') if usuario else None,
            usuario.get('username') if usuario else None,
            usuario.get('nome') if usuario else None,
            _upper(acao),
            _upper(entidade),
            registro_id or None,
            payload,
        ),
    )
    conn.commit()
    return log_id


def listar_logs_acoes(limit: int = 200, busca: str = None, acao: str = None, entidade: str = None):
    limit = max(1, min(int(limit or 200), 1000))
    where = []
    params = []
    busca = str(busca or '').strip()
    if busca:
        like = f"%{_upper(busca)}%"
        where.append("(UPPER(COALESCE(usuario_username, '')) LIKE ? OR UPPER(COALESCE(usuario_nome, '')) LIKE ? OR UPPER(COALESCE(registro_id, '')) LIKE ? OR UPPER(COALESCE(detalhes_json, '')) LIKE ?)")
        params.extend([like, like, like, like])
    if acao:
        where.append("UPPER(COALESCE(acao, '')) = ?")
        params.append(_upper(acao))
    if entidade:
        where.append("UPPER(COALESCE(entidade, '')) = ?")
        params.append(_upper(entidade))
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    cur = _cursor()
    cur.execute(f"SELECT * FROM audit_logs {where_sql} ORDER BY created_at DESC LIMIT ?", tuple(params + [limit]))
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        try:
            parsed = json.loads(item.get('detalhes_json') or '{}')
        except Exception:
            parsed = {}
        item['detalhes'] = parsed
        item['detalhes_texto'] = '\n'.join(f"{k}: {v}" for k, v in parsed.items() if v not in (None, '', []))
        rows.append(item)
    return rows


def _normalizar_email(email: str) -> str | None:
    email = str(email or '').strip().lower()
    return email or None


def _sugerir_username_por_nome(nome: str) -> str:
    base = _slug_username(nome)
    if not base:
        base = 'usuario'
    username = base
    cur = _cursor()
    seq = 1
    while True:
        cur.execute("SELECT id FROM usuarios WHERE lower(username) = ?", (username.lower(),))
        if not cur.fetchone():
            return username
        seq += 1
        username = f'{base}{seq}'


def _resolver_nome_exibicao_usuario(funcionario_id: str = None, nome: str = None):
    if funcionario_id:
        func = get_funcionario(funcionario_id)
        if not func:
            raise ValueError('Funcionário não encontrado.')
        return func.get('nome') or nome or '', func
    nome = _upper(nome)
    if not nome:
        raise ValueError('Informe o nome do usuário.')
    return nome, None


def autenticar_usuario(username: str, password: str):
    username = str(username or '').strip().lower()
    password = str(password or '').strip()
    if not username:
        return False, 'Informe o usuário.', None
    cur = _cursor()
    cur.execute(
        """
        SELECT u.*, f.nome AS nome_funcionario,
               COALESCE(u.nome, f.nome, u.username) AS nome_exibicao
        FROM usuarios u
        LEFT JOIN funcionarios f ON f.id = u.funcionario_id
        WHERE lower(u.username) = ? AND u.ativo = 1
        """,
        (username,),
    )
    row = cur.fetchone()
    if not row:
        return False, 'Usuário não encontrado.', None
    row = dict(row)
    if not _verify_password(password, row.get('password')):
        return False, 'Senha incorreta.', None
    if _needs_password_upgrade(row.get('password')):
        try:
            alterar_senha_usuario(row.get('id'), password, bool(row.get('deve_trocar_senha', 0)))
            cur.execute("SELECT * FROM usuarios WHERE id = ?", (row.get('id'),))
            row2 = cur.fetchone()
            if row2:
                row.update(dict(row2))
        except Exception:
            pass
    return True, '', row


def alterar_senha_usuario(usuario_id: str, nova_senha: str, deve_trocar: bool = False):
    if not usuario_id:
        raise ValueError('Usuário inválido.')
    if len(str(nova_senha or '').strip()) < 4:
        raise ValueError('A senha deve ter pelo menos 4 caracteres.')
    cur = _cursor()
    cur.execute(
        """
        UPDATE usuarios
        SET password = ?, deve_trocar_senha = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (_hash_password(nova_senha), 1 if deve_trocar else 0, usuario_id),
    )
    conn.commit()
    try:
        registrar_log_acao('ALTERAR_SENHA', 'USUARIO', usuario_id, {'deve_trocar_senha': bool(deve_trocar)})
    except Exception:
        pass


def listar_usuarios():
    cur = _cursor()
    cur.execute(
        """
        SELECT u.*, f.nome AS nome_funcionario, e.nome AS equipe_nome,
               COALESCE(u.nome, f.nome, u.username) AS nome_exibicao
        FROM usuarios u
        LEFT JOIN funcionarios f ON f.id = u.funcionario_id
        LEFT JOIN equipes e ON e.id = f.equipe_id
        ORDER BY u.username
        """
    )
    return [dict(r) for r in cur.fetchall()]


def sugerir_username_funcionario(funcionario_id: str) -> str:
    func = get_funcionario(funcionario_id)
    if not func:
        raise ValueError('Funcionário não encontrado.')
    return _sugerir_username_por_nome(func.get('nome'))


def criar_usuario(funcionario_id: str = None, nome: str = None, email: str = None, username: str = None,
                 nivel_acesso: str = 'VISUALIZACAO', ativo: bool = True, pode_ver_logs: bool = False,
                 enviar_email: bool = True):
    nome_exibicao, _func = _resolver_nome_exibicao_usuario(funcionario_id, nome)
    email = _normalizar_email(email)
    if not email:
        raise ValueError('Informe o e-mail do usuário.')
    cur = _cursor()
    if funcionario_id:
        cur.execute("SELECT id FROM usuarios WHERE funcionario_id = ?", (funcionario_id,))
        if cur.fetchone():
            raise ValueError('Este funcionário já possui usuário.')
    username = str(username or '').strip().lower() or (sugerir_username_funcionario(funcionario_id) if funcionario_id else _sugerir_username_por_nome(nome_exibicao))
    cur.execute("SELECT id FROM usuarios WHERE lower(username) = ?", (username.lower(),))
    if cur.fetchone():
        raise ValueError('O usuário informado já existe.')
    usuario_id = str(uuid.uuid4())
    senha_temporaria = _gerar_senha_temporaria()
    cur.execute(
        """
        INSERT INTO usuarios (id, funcionario_id, nome, email, username, password, nivel_acesso, ativo, deve_trocar_senha, pode_ver_logs, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            usuario_id,
            funcionario_id or None,
            nome_exibicao,
            email,
            username,
            _hash_password(senha_temporaria),
            _normalize_perfil_acesso(nivel_acesso) or 'VISUALIZACAO',
            1 if ativo else 0,
            1 if pode_ver_logs else 0,
        ),
    )
    conn.commit()
    email_enviado = False
    if enviar_email:
        try:
            email_enviado = enviar_credenciais_usuario_email(nome_exibicao, email, username, senha_temporaria)
        except Exception:
            email_enviado = False
    try:
        registrar_log_acao('CRIAR', 'USUARIO', usuario_id, {
            'username': username,
            'nome': nome_exibicao,
            'email': email,
            'tipo': 'FUNCIONARIO' if funcionario_id else 'EXTERNO',
            'nivel_acesso': _normalize_perfil_acesso(nivel_acesso),
            'pode_ver_logs': bool(pode_ver_logs),
            'email_enviado': email_enviado,
        })
    except Exception:
        pass
    return {'id': usuario_id, 'username': username, 'senha_temporaria': senha_temporaria, 'email_enviado': email_enviado}


def atualizar_usuario(usuario_id: str, funcionario_id: str = None, nome: str = None, email: str = None, username: str = None,
                     nivel_acesso: str = 'VISUALIZACAO', ativo: bool = True, pode_ver_logs: bool = False,
                     enviar_email: bool = False):
    cur = _cursor()
    cur.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
    atual = cur.fetchone()
    if not atual:
        raise ValueError('Usuário não encontrado.')
    atual = dict(atual)
    nome_exibicao, _func = _resolver_nome_exibicao_usuario(funcionario_id, nome or atual.get('nome'))
    email = _normalizar_email(email) or _normalizar_email(atual.get('email'))
    if not email:
        raise ValueError('Informe o e-mail do usuário.')
    if funcionario_id:
        cur.execute("SELECT id FROM usuarios WHERE funcionario_id = ? AND id <> ?", (funcionario_id, usuario_id))
        if cur.fetchone():
            raise ValueError('Este funcionário já possui outro usuário.')
    username = str(username or '').strip().lower() or str(atual.get('username') or '').strip().lower()
    if not username:
        username = sugerir_username_funcionario(funcionario_id) if funcionario_id else _sugerir_username_por_nome(nome_exibicao)
    cur.execute("SELECT id FROM usuarios WHERE lower(username) = ? AND id <> ?", (username.lower(), usuario_id))
    if cur.fetchone():
        raise ValueError('O usuário informado já existe.')
    cur.execute(
        """
        UPDATE usuarios
        SET funcionario_id = ?, nome = ?, email = ?, username = ?, nivel_acesso = ?, ativo = ?, pode_ver_logs = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            funcionario_id or None,
            nome_exibicao,
            email,
            username,
            _normalize_perfil_acesso(nivel_acesso) or 'VISUALIZACAO',
            1 if ativo else 0,
            1 if pode_ver_logs else 0,
            usuario_id,
        ),
    )
    conn.commit()
    email_enviado = False
    if enviar_email:
        resultado_reset = resetar_senha_usuario(usuario_id, enviar_email=True)
        email_enviado = bool(resultado_reset.get('email_enviado'))
    try:
        registrar_log_acao('ATUALIZAR', 'USUARIO', usuario_id, {
            'username': username,
            'nome': nome_exibicao,
            'email': email,
            'tipo': 'FUNCIONARIO' if funcionario_id else 'EXTERNO',
            'nivel_acesso': _normalize_perfil_acesso(nivel_acesso),
            'ativo': bool(ativo),
            'pode_ver_logs': bool(pode_ver_logs),
            'email_enviado': email_enviado,
        })
    except Exception:
        pass
    return {'id': usuario_id, 'username': username, 'email_enviado': email_enviado}


def resetar_senha_usuario(usuario_id: str, enviar_email: bool = True):
    cur = _cursor()
    cur.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Usuário não encontrado.')
    row = dict(row)
    senha_temporaria = _gerar_senha_temporaria()
    alterar_senha_usuario(usuario_id, senha_temporaria, True)
    email_enviado = False
    if enviar_email:
        email = _normalizar_email(row.get('email'))
        if not email:
            raise ValueError('Usuário sem e-mail cadastrado para envio de credenciais.')
        try:
            email_enviado = enviar_credenciais_usuario_email(row.get('nome') or row.get('username'), email, row.get('username'), senha_temporaria)
        except Exception:
            email_enviado = False
    try:
        registrar_log_acao('RESETAR_SENHA', 'USUARIO', usuario_id, {'email_enviado': email_enviado})
    except Exception:
        pass
    return {'id': usuario_id, 'username': row.get('username'), 'senha_temporaria': senha_temporaria, 'email_enviado': email_enviado}


def excluir_usuario(usuario_id: str):
    cur = _cursor()
    cur.execute("SELECT username FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    if row and str(row[0] or '').lower() == 'admin':
        raise ValueError('O usuário admin não pode ser excluído.')
    cur.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    conn.commit()
    try:
        registrar_log_acao('EXCLUIR', 'USUARIO', usuario_id, {'username': row[0] if row else None})
    except Exception:
        pass


def registrar_login_usuario(usuario_id: str):
    if usuario_id:
        try:
            registrar_log_acao('LOGIN', 'USUARIO', usuario_id, {})
        except Exception:
            pass


def registrar_logout_usuario(usuario_id: str):
    if usuario_id:
        try:
            registrar_log_acao('LOGOUT', 'USUARIO', usuario_id, {})
        except Exception:
            pass


def _audit_wrap(func_name: str, acao: str, entidade: str, id_from_result: bool = False, id_kw: str = None):
    original = globals().get(func_name)
    if not callable(original):
        return
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            registro_id = None
            if id_from_result:
                registro_id = result.get('id') if isinstance(result, dict) else result
            elif id_kw:
                if id_kw in kwargs:
                    registro_id = kwargs.get(id_kw)
                elif args:
                    registro_id = args[0]
            registrar_log_acao(acao, entidade, registro_id, {})
        except Exception:
            pass
        return result
    globals()[func_name] = wrapped


_audit_wrap('criar_ativo', 'CRIAR', 'ATIVO', id_from_result=True)
_audit_wrap('atualizar_ativo', 'ATUALIZAR', 'ATIVO', id_kw='ativo_id')
_audit_wrap('excluir_ativo', 'EXCLUIR', 'ATIVO', id_kw='ativo_id')
_audit_wrap('criar_os', 'CRIAR', 'OS', id_from_result=True)
_audit_wrap('atualizar_os', 'ATUALIZAR', 'OS', id_kw='os_id')
_audit_wrap('excluir_os', 'EXCLUIR', 'OS', id_kw='os_id')
_audit_wrap('criar_os_atividade', 'CRIAR', 'OS_ATIVIDADE', id_from_result=True)
_audit_wrap('atualizar_os_atividade', 'ATUALIZAR', 'OS_ATIVIDADE', id_kw='atividade_id')
_audit_wrap('excluir_os_atividade', 'EXCLUIR', 'OS_ATIVIDADE', id_kw='atividade_id')
_audit_wrap('criar_os_apontamento', 'CRIAR', 'OS_APONTAMENTO', id_from_result=True)
_audit_wrap('atualizar_os_apontamento', 'ATUALIZAR', 'OS_APONTAMENTO', id_kw='apontamento_id')
_audit_wrap('excluir_os_apontamento', 'EXCLUIR', 'OS_APONTAMENTO', id_kw='apontamento_id')
_audit_wrap('criar_os_material', 'CRIAR', 'OS_MATERIAL', id_from_result=True)
_audit_wrap('excluir_os_material', 'EXCLUIR', 'OS_MATERIAL', id_kw='material_id')
_audit_wrap('criar_equipe', 'CRIAR', 'EQUIPE', id_from_result=True)
_audit_wrap('atualizar_equipe', 'ATUALIZAR', 'EQUIPE', id_kw='equipe_id')
_audit_wrap('excluir_equipe', 'EXCLUIR', 'EQUIPE', id_kw='equipe_id')
_audit_wrap('criar_escala', 'CRIAR', 'ESCALA', id_from_result=True)
_audit_wrap('atualizar_escala', 'ATUALIZAR', 'ESCALA', id_kw='escala_id')
_audit_wrap('excluir_escala', 'EXCLUIR', 'ESCALA', id_kw='escala_id')
_audit_wrap('criar_funcionario', 'CRIAR', 'FUNCIONARIO', id_from_result=True)
_audit_wrap('atualizar_funcionario', 'ATUALIZAR', 'FUNCIONARIO', id_kw='funcionario_id')
_audit_wrap('excluir_funcionario', 'EXCLUIR', 'FUNCIONARIO', id_kw='funcionario_id')

# ===== FINAL PATCH - RESET POR LINK + EXPORT LOG SUPPORT (2026-04-21) =====
from datetime import datetime, timedelta


def _utc_now() -> datetime:
    return datetime.utcnow()


def _format_sqlite_dt(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _parse_db_datetime(value):
    raw = str(value or '').strip()
    if not raw:
        return None
    raw = raw.replace('T', ' ').replace('Z', '')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()


def _ensure_password_reset_schema():
    cur = _cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id TEXT PRIMARY KEY,
            usuario_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            finalidade TEXT NOT NULL DEFAULT 'RESET_SENHA',
            expires_at TEXT NOT NULL,
            used_at TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
        )
        """
    )
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_usuario_id ON password_reset_tokens(usuario_id)")
    except Exception:
        pass
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_expires_at ON password_reset_tokens(expires_at)")
    except Exception:
        pass
    conn.commit()


_ensure_password_reset_schema()


def _invalidate_password_reset_tokens(usuario_id: str):
    cur = _cursor()
    cur.execute(
        """
        UPDATE password_reset_tokens
        SET used_at = CURRENT_TIMESTAMP
        WHERE usuario_id = ? AND used_at IS NULL
        """,
        (usuario_id,),
    )
    conn.commit()


def criar_token_redefinicao_usuario(usuario_id: str, validade_horas: int = 24, finalidade: str = 'RESET_SENHA'):
    cur = _cursor()
    cur.execute("SELECT id, username, nome, email, ativo FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Usuário não encontrado.')
    row = dict(row)
    if not bool(row.get('ativo', 1)):
        raise ValueError('Usuário inativo.')
    email = _normalizar_email(row.get('email'))
    if not email:
        raise ValueError('Usuário sem e-mail cadastrado.')
    _invalidate_password_reset_tokens(usuario_id)
    token = secrets.token_urlsafe(32)
    token_hash = _sha256_hex(token)
    expires_at = _utc_now() + timedelta(hours=max(1, int(validade_horas or 24)))
    token_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO password_reset_tokens (id, usuario_id, token_hash, finalidade, expires_at, used_at, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)
        """,
        (token_id, usuario_id, token_hash, _upper(finalidade) or 'RESET_SENHA', _format_sqlite_dt(expires_at)),
    )
    conn.commit()
    base_url = (APP_BASE_URL or '').rstrip('/')
    link = f"{base_url}/definir-senha?token={token}" if base_url else f"/definir-senha?token={token}"
    return {
        'id': token_id,
        'token': token,
        'token_hash': token_hash,
        'link': link,
        'expires_at': _format_sqlite_dt(expires_at),
        'usuario': row,
    }


def enviar_link_redefinicao_usuario_email(nome: str, email: str, username: str, link: str, expires_at: str, motivo: str = 'definição de senha') -> bool:
    nome = str(nome or '').strip() or 'Usuário'
    corpo = f'''Olá, {nome}.

Recebemos uma solicitação de {motivo} para seu acesso ao Maintenance APP.

Usuário: {username}
Link seguro: {link}
Validade até: {expires_at}

Se você não reconhece esta ação, ignore este e-mail.
'''
    return _enviar_email(email, 'Link de acesso - Maintenance APP', corpo)


def validar_token_redefinicao(token: str):
    token = str(token or '').strip()
    if not token:
        return None
    cur = _cursor()
    cur.execute(
        """
        SELECT prt.*, u.username, u.nome, u.email, u.ativo
        FROM password_reset_tokens prt
        JOIN usuarios u ON u.id = prt.usuario_id
        WHERE prt.token_hash = ?
        LIMIT 1
        """,
        (_sha256_hex(token),),
    )
    row = cur.fetchone()
    if not row:
        return None
    item = dict(row)
    if item.get('used_at'):
        return None
    expires_at = _parse_db_datetime(item.get('expires_at'))
    if not expires_at or expires_at < _utc_now():
        return None
    if not bool(item.get('ativo', 1)):
        return None
    item['nome_exibicao'] = item.get('nome') or item.get('username')
    return item


def consumir_token_redefinicao(token: str, nova_senha: str):
    info = validar_token_redefinicao(token)
    if not info:
        raise ValueError('Link inválido, expirado ou já utilizado.')
    alterar_senha_usuario(info['usuario_id'], nova_senha, False)
    cur = _cursor()
    cur.execute("UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP WHERE id = ?", (info['id'],))
    conn.commit()
    try:
        registrar_log_acao('CONSUMIR_LINK_SENHA', 'USUARIO', info['usuario_id'], {'token_id': info['id']}, usuario_id=info['usuario_id'])
    except Exception:
        pass
    return {'usuario_id': info['usuario_id'], 'username': info.get('username')}


def criar_usuario(funcionario_id: str = None, nome: str = None, email: str = None, username: str = None,
                 nivel_acesso: str = 'VISUALIZACAO', ativo: bool = True, pode_ver_logs: bool = False,
                 enviar_email: bool = True):
    nome_exibicao, _func = _resolver_nome_exibicao_usuario(funcionario_id, nome)
    email = _normalizar_email(email)
    if not email:
        raise ValueError('Informe o e-mail do usuário.')
    cur = _cursor()
    if funcionario_id:
        cur.execute("SELECT id FROM usuarios WHERE funcionario_id = ?", (funcionario_id,))
        if cur.fetchone():
            raise ValueError('Este funcionário já possui usuário.')
    username = str(username or '').strip().lower() or (sugerir_username_funcionario(funcionario_id) if funcionario_id else _sugerir_username_por_nome(nome_exibicao))
    cur.execute("SELECT id FROM usuarios WHERE lower(username) = ?", (username.lower(),))
    if cur.fetchone():
        raise ValueError('O usuário informado já existe.')
    usuario_id = str(uuid.uuid4())
    senha_placeholder = _gerar_senha_temporaria()
    cur.execute(
        """
        INSERT INTO usuarios (id, funcionario_id, nome, email, username, password, nivel_acesso, ativo, deve_trocar_senha, pode_ver_logs, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (usuario_id, funcionario_id or None, nome_exibicao, email, username, _hash_password(senha_placeholder), _normalize_perfil_acesso(nivel_acesso) or 'VISUALIZACAO', 1 if ativo else 0, 1 if pode_ver_logs else 0),
    )
    conn.commit()
    email_enviado = False
    link_redefinicao = None
    if enviar_email:
        try:
            token_data = criar_token_redefinicao_usuario(usuario_id, validade_horas=24, finalidade='ATIVACAO_USUARIO')
            link_redefinicao = token_data.get('link')
            email_enviado = enviar_link_redefinicao_usuario_email(nome_exibicao, email, username, token_data['link'], token_data['expires_at'], 'definição de senha')
        except Exception:
            email_enviado = False
    try:
        registrar_log_acao('CRIAR', 'USUARIO', usuario_id, {'username': username, 'nome': nome_exibicao, 'email': email, 'tipo': 'FUNCIONARIO' if funcionario_id else 'EXTERNO', 'nivel_acesso': _normalize_perfil_acesso(nivel_acesso), 'pode_ver_logs': bool(pode_ver_logs), 'email_enviado': email_enviado, 'link_redefinicao_gerado': bool(link_redefinicao)})
    except Exception:
        pass
    return {'id': usuario_id, 'username': username, 'email_enviado': email_enviado, 'link_redefinicao': link_redefinicao}


def resetar_senha_usuario(usuario_id: str, enviar_email: bool = True):
    cur = _cursor()
    cur.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Usuário não encontrado.')
    row = dict(row)
    email = _normalizar_email(row.get('email'))
    if enviar_email and not email:
        raise ValueError('Usuário sem e-mail cadastrado para envio do link.')
    token_data = criar_token_redefinicao_usuario(usuario_id, validade_horas=24, finalidade='RESET_SENHA')
    email_enviado = False
    if enviar_email:
        try:
            email_enviado = enviar_link_redefinicao_usuario_email(row.get('nome') or row.get('username'), email, row.get('username'), token_data['link'], token_data['expires_at'], 'redefinição de senha')
        except Exception:
            email_enviado = False
    try:
        registrar_log_acao('RESETAR_SENHA', 'USUARIO', usuario_id, {'email_enviado': email_enviado, 'token_id': token_data['id']})
    except Exception:
        pass
    return {'id': usuario_id, 'username': row.get('username'), 'email_enviado': email_enviado, 'link_redefinicao': token_data['link']}


# ===== PATCH v2: LOGS DETALHADOS POR PERFIS (2026-04-21) ===================
# Substitui os _audit_wrap genéricos por funções de log enriquecidas.
# Captura campos reais (tag, descrição, número OS, funcionário etc.)
# sem alterar nenhuma tabela existente.

def _log_ativo(acao: str, ativo_id: str, dados: dict | None = None):
    """Log enriquecido para ativos/equipamentos."""
    detalhes = {}
    try:
        cur = _cursor()
        cur.execute("SELECT tag, descricao, tipo FROM ativos WHERE id = ?", (ativo_id,))
        row = cur.fetchone()
        if row:
            r = dict(row)
            detalhes = {'tag': r.get('tag'), 'descricao': r.get('descricao'), 'tipo': r.get('tipo')}
    except Exception:
        pass
    if dados:
        detalhes.update(dados)
    try:
        registrar_log_acao(acao, 'ATIVO', ativo_id, detalhes)
    except Exception:
        pass


def _log_os(acao: str, os_id: str, dados: dict | None = None):
    """Log enriquecido para ordens de serviço."""
    detalhes = {}
    try:
        cur = _cursor()
        cur.execute(
            """SELECT os.numero, os.descricao, os.status, a.tag AS equipamento_tag
               FROM ordens_servico os
               LEFT JOIN ativos a ON a.id = os.equipamento_id
               WHERE os.id = ?""",
            (os_id,),
        )
        row = cur.fetchone()
        if row:
            r = dict(row)
            detalhes = {
                'numero_os': r.get('numero'),
                'descricao': r.get('descricao'),
                'status': r.get('status'),
                'equipamento_tag': r.get('equipamento_tag'),
            }
    except Exception:
        pass
    if dados:
        detalhes.update(dados)
    try:
        registrar_log_acao(acao, 'OS', os_id, detalhes)
    except Exception:
        pass


def _log_equipe(acao: str, equipe_id: str):
    detalhes = {}
    try:
        cur = _cursor()
        cur.execute("SELECT nome FROM equipes WHERE id = ?", (equipe_id,))
        row = cur.fetchone()
        if row:
            detalhes = {'nome': dict(row).get('nome')}
    except Exception:
        pass
    try:
        registrar_log_acao(acao, 'EQUIPE', equipe_id, detalhes)
    except Exception:
        pass


def _log_funcionario(acao: str, funcionario_id: str):
    detalhes = {}
    try:
        cur = _cursor()
        cur.execute("SELECT nome, matricula FROM funcionarios WHERE id = ?", (funcionario_id,))
        row = cur.fetchone()
        if row:
            r = dict(row)
            detalhes = {'nome': r.get('nome'), 'matricula': r.get('matricula')}
    except Exception:
        pass
    try:
        registrar_log_acao(acao, 'FUNCIONARIO', funcionario_id, detalhes)
    except Exception:
        pass


def _patch_log_rico():
    """
    Sobrescreve os wrappers genéricos (_audit_wrap) com versões que capturam
    detalhes reais do banco. Chamado uma vez na inicialização.
    """
    import functools

    # ── ATIVO ────────────────────────────────────────────────────────────────
    _criar_ativo_orig = globals().get('criar_ativo')
    if callable(_criar_ativo_orig):
        @functools.wraps(_criar_ativo_orig)
        def _criar_ativo_logged(*a, **kw):
            result = _criar_ativo_orig(*a, **kw)
            ativo_id = result.get('id') if isinstance(result, dict) else result
            tag = kw.get('tag') or (a[0] if a else None)
            descricao = kw.get('descricao') or (a[1] if len(a) > 1 else None)
            tipo = kw.get('tipo') or (a[2] if len(a) > 2 else None)
            try:
                registrar_log_acao('CRIAR', 'ATIVO', ativo_id,
                    {'tag': tag, 'descricao': descricao, 'tipo': tipo})
            except Exception:
                pass
            return result
        globals()['criar_ativo'] = _criar_ativo_logged

    _atualizar_ativo_orig = globals().get('atualizar_ativo')
    if callable(_atualizar_ativo_orig):
        @functools.wraps(_atualizar_ativo_orig)
        def _atualizar_ativo_logged(*a, **kw):
            ativo_id = kw.get('ativo_id') or (a[0] if a else None)
            # captura antes de alterar
            detalhes_antes = {}
            try:
                cur = _cursor()
                cur.execute("SELECT tag, descricao, tipo FROM ativos WHERE id = ?", (ativo_id,))
                row = cur.fetchone()
                if row:
                    r = dict(row)
                    detalhes_antes = {'tag_antes': r.get('tag'), 'descricao_antes': r.get('descricao')}
            except Exception:
                pass
            result = _atualizar_ativo_orig(*a, **kw)
            tag_novo = kw.get('tag')
            try:
                registrar_log_acao('ATUALIZAR', 'ATIVO', ativo_id,
                    {**detalhes_antes, 'tag_novo': tag_novo, 'descricao_nova': kw.get('descricao')})
            except Exception:
                pass
            return result
        globals()['atualizar_ativo'] = _atualizar_ativo_logged

    _excluir_ativo_orig = globals().get('excluir_ativo')
    if callable(_excluir_ativo_orig):
        @functools.wraps(_excluir_ativo_orig)
        def _excluir_ativo_logged(*a, **kw):
            ativo_id = kw.get('ativo_id') or (a[0] if a else None)
            detalhes = {}
            try:
                cur = _cursor()
                cur.execute("SELECT tag, descricao, tipo FROM ativos WHERE id = ?", (ativo_id,))
                row = cur.fetchone()
                if row:
                    r = dict(row)
                    detalhes = {'tag': r.get('tag'), 'descricao': r.get('descricao'), 'tipo': r.get('tipo')}
            except Exception:
                pass
            result = _excluir_ativo_orig(*a, **kw)
            try:
                registrar_log_acao('EXCLUIR', 'ATIVO', ativo_id, detalhes)
            except Exception:
                pass
            return result
        globals()['excluir_ativo'] = _excluir_ativo_logged

    # ── OS ───────────────────────────────────────────────────────────────────
    _criar_os_orig = globals().get('criar_os')
    if callable(_criar_os_orig):
        @functools.wraps(_criar_os_orig)
        def _criar_os_logged(*a, **kw):
            result = _criar_os_orig(*a, **kw)
            os_id = result.get('id') if isinstance(result, dict) else result
            _log_os('CRIAR', os_id, {
                'descricao': kw.get('descricao') or (a[1] if len(a) > 1 else None),
                'tipo_os': kw.get('tipo_os'),
                'prioridade': kw.get('prioridade'),
            })
            return result
        globals()['criar_os'] = _criar_os_logged

    _atualizar_os_orig = globals().get('atualizar_os')
    if callable(_atualizar_os_orig):
        @functools.wraps(_atualizar_os_orig)
        def _atualizar_os_logged(*a, **kw):
            os_id = kw.get('os_id') or (a[0] if a else None)
            # captura status antes
            status_antes = None
            try:
                cur = _cursor()
                cur.execute("SELECT status FROM ordens_servico WHERE id = ?", (os_id,))
                row = cur.fetchone()
                status_antes = dict(row).get('status') if row else None
            except Exception:
                pass
            result = _atualizar_os_orig(*a, **kw)
            novo_status = kw.get('status') or status_antes
            acao = 'ENCERRAR' if (str(novo_status or '').upper() == 'ENCERRADA' and status_antes != 'ENCERRADA') else 'ATUALIZAR'
            _log_os(acao, os_id, {
                'status_anterior': status_antes,
                'status_novo': novo_status,
                'justificativa_encerramento': kw.get('justificativa_encerramento'),
            })
            return result
        globals()['atualizar_os'] = _atualizar_os_logged

    _excluir_os_orig = globals().get('excluir_os')
    if callable(_excluir_os_orig):
        @functools.wraps(_excluir_os_orig)
        def _excluir_os_logged(*a, **kw):
            os_id = kw.get('os_id') or (a[0] if a else None)
            detalhes = {}
            try:
                cur = _cursor()
                cur.execute("SELECT numero, descricao FROM ordens_servico WHERE id = ?", (os_id,))
                row = cur.fetchone()
                if row:
                    r = dict(row)
                    detalhes = {'numero_os': r.get('numero'), 'descricao': r.get('descricao')}
            except Exception:
                pass
            result = _excluir_os_orig(*a, **kw)
            try:
                registrar_log_acao('EXCLUIR', 'OS', os_id, detalhes)
            except Exception:
                pass
            return result
        globals()['excluir_os'] = _excluir_os_logged

    # ── EQUIPE ───────────────────────────────────────────────────────────────
    _criar_equipe_orig = globals().get('criar_equipe')
    if callable(_criar_equipe_orig):
        @functools.wraps(_criar_equipe_orig)
        def _criar_equipe_logged(*a, **kw):
            result = _criar_equipe_orig(*a, **kw)
            equipe_id = result.get('id') if isinstance(result, dict) else result
            nome = kw.get('nome') or (a[0] if a else None)
            try:
                registrar_log_acao('CRIAR', 'EQUIPE', equipe_id, {'nome': nome})
            except Exception:
                pass
            return result
        globals()['criar_equipe'] = _criar_equipe_logged

    _atualizar_equipe_orig = globals().get('atualizar_equipe')
    if callable(_atualizar_equipe_orig):
        @functools.wraps(_atualizar_equipe_orig)
        def _atualizar_equipe_logged(*a, **kw):
            equipe_id = kw.get('equipe_id') or (a[0] if a else None)
            result = _atualizar_equipe_orig(*a, **kw)
            nome = kw.get('nome') or (a[1] if len(a) > 1 else None)
            try:
                registrar_log_acao('ATUALIZAR', 'EQUIPE', equipe_id, {'nome': nome})
            except Exception:
                pass
            return result
        globals()['atualizar_equipe'] = _atualizar_equipe_logged

    _excluir_equipe_orig = globals().get('excluir_equipe')
    if callable(_excluir_equipe_orig):
        @functools.wraps(_excluir_equipe_orig)
        def _excluir_equipe_logged(*a, **kw):
            equipe_id = kw.get('equipe_id') or (a[0] if a else None)
            _log_equipe('EXCLUIR', equipe_id)
            result = _excluir_equipe_orig(*a, **kw)
            return result
        globals()['excluir_equipe'] = _excluir_equipe_logged

    # ── FUNCIONÁRIO ──────────────────────────────────────────────────────────
    _criar_funcionario_orig = globals().get('criar_funcionario')
    if callable(_criar_funcionario_orig):
        @functools.wraps(_criar_funcionario_orig)
        def _criar_funcionario_logged(*a, **kw):
            result = _criar_funcionario_orig(*a, **kw)
            func_id = result.get('id') if isinstance(result, dict) else result
            nome = kw.get('nome') or (a[0] if a else None)
            matricula = kw.get('matricula') or (a[1] if len(a) > 1 else None)
            try:
                registrar_log_acao('CRIAR', 'FUNCIONARIO', func_id,
                    {'nome': nome, 'matricula': matricula})
            except Exception:
                pass
            return result
        globals()['criar_funcionario'] = _criar_funcionario_logged

    _atualizar_funcionario_orig = globals().get('atualizar_funcionario')
    if callable(_atualizar_funcionario_orig):
        @functools.wraps(_atualizar_funcionario_orig)
        def _atualizar_funcionario_logged(*a, **kw):
            func_id = kw.get('funcionario_id') or (a[0] if a else None)
            result = _atualizar_funcionario_orig(*a, **kw)
            nome = kw.get('nome') or (a[1] if len(a) > 1 else None)
            try:
                registrar_log_acao('ATUALIZAR', 'FUNCIONARIO', func_id,
                    {'nome': nome, 'matricula': kw.get('matricula')})
            except Exception:
                pass
            return result
        globals()['atualizar_funcionario'] = _atualizar_funcionario_logged

    _excluir_funcionario_orig = globals().get('excluir_funcionario')
    if callable(_excluir_funcionario_orig):
        @functools.wraps(_excluir_funcionario_orig)
        def _excluir_funcionario_logged(*a, **kw):
            func_id = kw.get('funcionario_id') or (a[0] if a else None)
            _log_funcionario('EXCLUIR', func_id)  # captura antes de excluir
            result = _excluir_funcionario_orig(*a, **kw)
            return result
        globals()['excluir_funcionario'] = _excluir_funcionario_logged

    # ── APONTAMENTOS / ATIVIDADES / MATERIAIS ── (mantém detalhes básicos) ──
    for fn, acao, ent, id_kw in [
        ('criar_os_atividade',   'CRIAR',    'OS_ATIVIDADE',   None),
        ('atualizar_os_atividade','ATUALIZAR','OS_ATIVIDADE',  'atividade_id'),
        ('excluir_os_atividade', 'EXCLUIR',  'OS_ATIVIDADE',   'atividade_id'),
        ('criar_os_apontamento', 'CRIAR',    'OS_APONTAMENTO', None),
        ('atualizar_os_apontamento','ATUALIZAR','OS_APONTAMENTO','apontamento_id'),
        ('excluir_os_apontamento','EXCLUIR', 'OS_APONTAMENTO', 'apontamento_id'),
        ('criar_os_material',    'CRIAR',    'OS_MATERIAL',    None),
        ('excluir_os_material',  'EXCLUIR',  'OS_MATERIAL',    'material_id'),
        ('criar_escala',         'CRIAR',    'ESCALA',         None),
        ('atualizar_escala',     'ATUALIZAR','ESCALA',         'escala_id'),
        ('excluir_escala',       'EXCLUIR',  'ESCALA',         'escala_id'),
    ]:
        orig = globals().get(fn)
        if not callable(orig):
            continue
        def _make_wrapper(orig_fn, _acao, _ent, _id_kw):
            @functools.wraps(orig_fn)
            def _w(*a, **kw):
                result = orig_fn(*a, **kw)
                try:
                    reg_id = None
                    if _id_kw:
                        reg_id = kw.get(_id_kw) or (a[0] if a else None)
                    elif isinstance(result, dict):
                        reg_id = result.get('id')
                    registrar_log_acao(_acao, _ent, reg_id, {
                        'os_id': kw.get('os_id') or (a[0] if a else None),
                    })
                except Exception:
                    pass
                return result
            return _w
        globals()[fn] = _make_wrapper(orig, acao, ent, id_kw)


# Executa o patch de logs enriquecidos na inicialização do módulo
try:
    _patch_log_rico()
except Exception as _e:
    print(f'[WARN] _patch_log_rico falhou: {_e}')

# ===== FIM DO PATCH v2 ======================================================


# ===== MIGRAÇÃO v2: SUPORTE A PERFIS ADMIN / GERENCIA / TECNICO =============
# Atualiza a coluna nivel_acesso para aceitar os novos perfis.
# Totalmente seguro: não apaga dados existentes.

def _migrar_perfis_v2():
    """
    1. Converte perfis legados: COMPLETO → GESTOR, VISUALIZACAO/TECNICO → EXECUTOR.
    2. Eleva o usuário 'admin' para ADMIN.
    3. Não pode travar a inicialização do app.
    Roda em cada start — idempotente.
    """
    local_conn = None
    local_cur = None
    try:
        print('[DB][startup] _migrar_perfis_v2...', flush=True)
        local_conn = get_connection()
        local_cur = local_conn.cursor()

        try:
            local_cur.execute("SET statement_timeout = '5000'")
        except Exception:
            pass

        try:
            local_cur.execute("SET lock_timeout = '3000'")
        except Exception:
            pass

        local_cur.execute(
            "UPDATE usuarios SET nivel_acesso = 'GESTOR', updated_at = CURRENT_TIMESTAMP "
            "WHERE UPPER(COALESCE(nivel_acesso,'')) = 'COMPLETO'"
        )

        local_cur.execute(
            "UPDATE usuarios SET nivel_acesso = 'EXECUTOR', updated_at = CURRENT_TIMESTAMP "
            "WHERE UPPER(COALESCE(nivel_acesso,'')) IN ('VISUALIZACAO','TECNICO')"
        )

        try:
            local_cur.execute(
                "UPDATE usuarios SET nivel_acesso = 'ADMIN', updated_at = CURRENT_TIMESTAMP "
                "WHERE LOWER(COALESCE(username,'')) = 'admin'"
            )
        except Exception as exc:
            print(f'[DB][startup] aviso ao promover admin em _migrar_perfis_v2: {exc}', flush=True)

        try:
            local_conn.commit()
        except Exception:
            pass

        print('[DB][startup] _migrar_perfis_v2 ok', flush=True)
    except Exception as exc:
        try:
            if local_conn:
                local_conn.rollback()
        except Exception:
            pass
        print(f'[DB][startup] _migrar_perfis_v2 ignorada: {exc}', flush=True)
    finally:
        try:
            if local_cur:
                local_cur.close()
        except Exception:
            pass


try:
    _migrar_perfis_v2()
except Exception as _me2:
    print(f'[DB][startup] falha em _migrar_perfis_v2: {_me2}', flush=True)

# ===== FIM DA MIGRAÇÃO v2 ====================================================


# ===== PATCH: criar / renomear / excluir perfil personalizável ==============

def criar_perfil_acesso(nome: str, descricao: str = '') -> dict:
    """Cria um novo perfil (linha em perfis_acesso + linhas zeradas em perfil_permissoes)."""
    nome = str(nome or '').strip().upper()
    if not nome:
        raise ValueError('Informe o nome do perfil.')
    cur = _cursor()
    cur.execute('SELECT nome FROM perfis_acesso WHERE UPPER(nome) = ?', (nome,))
    if cur.fetchone():
        raise ValueError(f'Já existe um perfil com o nome "{nome}".')
    cur.execute(
        'INSERT INTO perfis_acesso (nome, descricao, ativo, created_at, updated_at) VALUES (?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)',
        (nome, str(descricao or '').strip()),
    )
    zero = {k: 0 for k in CAMPOS_PERMISSAO_SISTEMA}
    for modulo in MODULOS_SISTEMA:
        cur.execute(
            f"INSERT OR IGNORE INTO perfil_permissoes (id, perfil_nome, modulo, {', '.join(CAMPOS_PERMISSAO_SISTEMA)}, created_at, updated_at) VALUES (?, ?, ?, {', '.join(['?']*len(CAMPOS_PERMISSAO_SISTEMA))}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (str(uuid.uuid4()), nome, modulo, *[0]*len(CAMPOS_PERMISSAO_SISTEMA)),
        )
    conn.commit()
    try:
        registrar_log_acao('CRIAR', 'PERFIL', None, {'nome': nome, 'descricao': descricao})
    except Exception:
        pass
    return {'nome': nome}


def renomear_perfil_acesso(nome_atual: str, nome_novo: str, descricao: str = '') -> dict:
    """Renomeia um perfil customizado e atualiza todas as referências."""
    nome_atual = str(nome_atual or '').strip().upper()
    nome_novo  = str(nome_novo or '').strip().upper()
    if nome_atual in ('ADMIN',):
        raise ValueError('O perfil ADMIN não pode ser renomeado.')
    if not nome_novo:
        raise ValueError('Informe o novo nome do perfil.')
    cur = _cursor()
    if nome_atual != nome_novo:
        cur.execute('SELECT nome FROM perfis_acesso WHERE UPPER(nome) = ?', (nome_novo,))
        if cur.fetchone():
            raise ValueError(f'Já existe um perfil com o nome "{nome_novo}".')
        cur.execute('UPDATE perfil_permissoes SET perfil_nome = ? WHERE UPPER(perfil_nome) = ?', (nome_novo, nome_atual))
        cur.execute('UPDATE usuarios SET nivel_acesso = ? WHERE UPPER(nivel_acesso) = ?', (nome_novo, nome_atual))
    cur.execute('UPDATE perfis_acesso SET nome = ?, descricao = ?, updated_at = CURRENT_TIMESTAMP WHERE UPPER(nome) = ?',
                (nome_novo, str(descricao or '').strip(), nome_atual))
    conn.commit()
    try:
        registrar_log_acao('ATUALIZAR', 'PERFIL', None, {'nome_antigo': nome_atual, 'nome_novo': nome_novo})
    except Exception:
        pass
    return {'nome': nome_novo}


def excluir_perfil_acesso(nome: str) -> None:
    """Exclui um perfil customizado. Bloqueia se houver usuários vinculados."""
    nome = str(nome or '').strip().upper()
    if nome in PERFIS_ACESSO_PADRAO:
        raise ValueError(f'O perfil "{nome}" é padrão do sistema e não pode ser excluído.')
    cur = _cursor()
    cur.execute('SELECT COUNT(*) AS c FROM usuarios WHERE UPPER(nivel_acesso) = ?', (nome,))
    row = cur.fetchone()
    cnt = dict(row).get('c', 0) if row else 0
    if cnt:
        raise ValueError(f'O perfil "{nome}" está em uso por {cnt} usuário(s). Remova-os antes de excluir.')
    cur.execute('DELETE FROM perfil_permissoes WHERE UPPER(perfil_nome) = ?', (nome,))
    cur.execute('DELETE FROM perfis_acesso WHERE UPPER(nome) = ?', (nome,))
    conn.commit()
    try:
        registrar_log_acao('EXCLUIR', 'PERFIL', None, {'nome': nome})
    except Exception:
        pass


def salvar_permissoes_perfil_completo(perfil_nome: str, modulos_payload: dict) -> None:
    """
    Salva todas as permissões de um perfil de uma vez.
    modulos_payload: { 'HOME': {'ver_menu': True, ...}, 'OS': {...}, ... }
    """
    perfil_nome_upper = str(perfil_nome or '').strip().upper()
    if perfil_nome_upper == 'ADMIN':
        raise ValueError('O perfil ADMIN é fixo e não pode ser alterado.')
    cur = _cursor()
    for modulo, perms in modulos_payload.items():
        modulo = str(modulo or '').strip().upper()
        if modulo not in MODULOS_SISTEMA:
            continue
        payload = {campo: 1 if bool((perms or {}).get(campo)) else 0 for campo in CAMPOS_PERMISSAO_SISTEMA}
        cur.execute(
            f"UPDATE perfil_permissoes SET {', '.join([f'{c} = ?' for c in CAMPOS_PERMISSAO_SISTEMA])}, updated_at = CURRENT_TIMESTAMP WHERE UPPER(perfil_nome) = ? AND modulo = ?",
            (*[payload[c] for c in CAMPOS_PERMISSAO_SISTEMA], perfil_nome_upper, modulo),
        )
    conn.commit()
    try:
        registrar_log_acao('ATUALIZAR', 'PERFIL', None, {
            'tipo': 'PERMISSOES_COMPLETO',
            'perfil_nome': perfil_nome_upper,
            'modulos': list(modulos_payload.keys()),
        })
    except Exception:
        pass

# ===== FIM DO PATCH ==========================================================


def close_connection(conn_to_close=None):
    target = conn_to_close
    if target is None:
        target = globals().get('conn')
    try:
        if target:
            target.close()
    except Exception as e:
        print(f'[DB] erro ao fechar conexão: {e}', flush=True)

# ===== PATCH EDUARDO 2026-04-29: FUSO CG-MS / LOG ERROS / GESTAO DADOS / OS =====
CAMPO_GRANDE_TZ = 'America/Campo_Grande'

def _agora_cg_iso() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(CAMPO_GRANDE_TZ)).replace(microsecond=0).isoformat(sep=' ')
    except Exception:
        return datetime.now().replace(microsecond=0).isoformat(sep=' ')

def _agora_sql() -> str:
    return _agora_cg_iso()

def _rollback_safe():
    try: get_connection().rollback()
    except Exception: pass

def _table_exists(table_name: str) -> bool:
    try:
        cur = _cursor(); cur.execute(f'PRAGMA table_info({table_name})')
        return bool(cur.fetchall())
    except Exception:
        return False

def _ensure_system_error_schema():
    cur = _cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_error_logs (
            id TEXT PRIMARY KEY, origem TEXT NOT NULL, mensagem TEXT NOT NULL,
            traceback TEXT NULL, contexto_json TEXT NULL, usuario_id TEXT NULL,
            resolvido INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cur.execute('CREATE INDEX IF NOT EXISTS idx_system_error_logs_created_at ON system_error_logs(created_at)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_system_error_logs_origem ON system_error_logs(origem)')
    except Exception: pass
    conn.commit()

def registrar_erro_sistema(origem: str, erro, contexto: dict | None = None, usuario_id: str | None = None):
    try:
        import traceback, json as _json
        if not _table_exists('system_error_logs'): _ensure_system_error_schema()
        cur = _cursor()
        cur.execute("""
            INSERT INTO system_error_logs (id, origem, mensagem, traceback, contexto_json, usuario_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), str(origem or 'SISTEMA')[:120], str(erro)[:4000], traceback.format_exc()[:12000], _json.dumps(contexto or {}, ensure_ascii=False), usuario_id, _agora_sql()))
        conn.commit()
    except Exception:
        _rollback_safe()

def listar_erros_sistema(limit: int = 300):
    try:
        _ensure_system_error_schema()
        cur = _cursor(); cur.execute('SELECT * FROM system_error_logs ORDER BY created_at DESC LIMIT ?', (int(limit or 300),))
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        _rollback_safe(); return []

def _ensure_gestao_dados_permissions():
    global MODULOS_SISTEMA
    try:
        if 'GESTAO_DADOS' not in MODULOS_SISTEMA:
            MODULOS_SISTEMA.append('GESTAO_DADOS')
        _ensure_access_control_schema()
        cur = _cursor()
        for perfil in listar_perfis_acesso():
            nome = str(perfil.get('nome') or '').upper()
            vals = {c: 0 for c in CAMPOS_PERMISSAO_SISTEMA}
            if nome == 'ADMIN': vals = {c: 1 for c in CAMPOS_PERMISSAO_SISTEMA}
            cur.execute(f"INSERT INTO perfil_permissoes (id, perfil_nome, modulo, {', '.join(CAMPOS_PERMISSAO_SISTEMA)}, created_at, updated_at) VALUES (?, ?, 'GESTAO_DADOS', {', '.join(['?']*len(CAMPOS_PERMISSAO_SISTEMA))}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT (perfil_nome, modulo) DO NOTHING", (str(uuid.uuid4()), nome, *[vals[c] for c in CAMPOS_PERMISSAO_SISTEMA]))
        conn.commit()
    except Exception as ex:
        _rollback_safe(); registrar_erro_sistema('PERMISSOES_GESTAO_DADOS', ex)

def listar_pecas_ativo_para_material(ativo_id: str):
    try:
        import json as _json
        cur = _cursor(); cur.execute('SELECT pecas_json FROM ativos WHERE id = ?', (ativo_id,))
        itens=[]
        for row in cur.fetchall():
            txt = dict(row).get('pecas_json')
            if not txt: continue
            try: data=_json.loads(txt)
            except Exception: data=[]
            if isinstance(data, list):
                for peca in data:
                    if isinstance(peca, dict):
                        nome = peca.get('descricao') or peca.get('nome') or peca.get('peca') or peca.get('tag')
                        qtd = peca.get('quantidade') or peca.get('qtd') or ''
                        if nome: itens.append(f"{str(nome).upper()}{(' - ' + str(qtd) + ' PÇS') if qtd not in ('', None) else ''}")
                    elif peca: itens.append(str(peca).upper())
        return list(dict.fromkeys(itens))
    except Exception as ex:
        _rollback_safe(); registrar_erro_sistema('LISTAR_PECAS_MATERIAL', ex, {'ativo_id': ativo_id}); return []

def listar_tabela_generica(tabela: str, limit: int = 5000):
    permitidas = {'ativos','ordens_servico','os_atividades','os_materiais','funcionarios','equipes','usuarios','audit_logs','system_error_logs'}
    tabela = str(tabela or '').strip()
    if tabela not in permitidas: raise ValueError('Tabela não liberada para exportação.')
    cur = _cursor(); cur.execute(f'SELECT * FROM {tabela} LIMIT ?', (int(limit or 5000),))
    return [dict(r) for r in cur.fetchall()]

def colunas_tabela_generica(tabela: str):
    return sorted(_get_columns(tabela))

def importar_tabela_generica(tabela: str, linhas: list[dict], modo: str = 'upsert'):
    permitidas = {'ativos','funcionarios','equipes'}
    tabela = str(tabela or '').strip()
    if tabela not in permitidas: raise ValueError('Importação liberada somente para ativos, funcionários e equipes.')
    cols_db = _get_columns(tabela)
    if not linhas: return {'linhas': 0}
    cur = _cursor(); count=0
    for item in linhas:
        payload = {k: v for k,v in (item or {}).items() if k in cols_db and k not in {'created_at','updated_at'}}
        if not payload: continue
        if not payload.get('id'): payload['id'] = str(uuid.uuid4())
        if 'updated_at' in cols_db: payload['updated_at'] = _agora_sql()
        if 'created_at' in cols_db and not payload.get('created_at'): payload['created_at'] = _agora_sql()
        cols=list(payload.keys()); vals=[payload[c] for c in cols]
        if modo == 'insert':
            cur.execute(f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})", tuple(vals))
        else:
            update_cols=[c for c in cols if c!='id']
            cur.execute(f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))}) ON CONFLICT (id) DO UPDATE SET {', '.join([c+' = EXCLUDED.'+c for c in update_cols])}", tuple(vals))
        count += 1
    conn.commit(); return {'linhas': count}

def dashboard_retrabalho(periodo_inicio: Optional[str] = None, periodo_fim: Optional[str] = None, equipamento_id: Optional[str] = None, limit: int = 20):
    cur = _cursor(); filtro_periodo, params = _dashboard_periodo_equipamento_filter(periodo_inicio, periodo_fim, equipamento_id, 'os')
    cur.execute(f"""
        SELECT os.equipamento_id, os.componente_id, eq.tag AS equipamento_tag, eq.descricao AS equipamento_descricao,
               cp.tag AS componente_tag, cp.descricao AS componente_descricao, COUNT(os.id) AS qtd_os_geral,
               SUM(CASE WHEN UPPER(COALESCE(os.status,'')) = 'ABERTA' THEN 1 ELSE 0 END) AS qtd_aberta,
               SUM(CASE WHEN UPPER(COALESCE(os.status,'')) IN ('EM EXECUÇÃO','EM EXECUCAO') THEN 1 ELSE 0 END) AS qtd_execucao,
               SUM(CASE WHEN UPPER(COALESCE(os.status,'')) = 'ENCERRADA' THEN 1 ELSE 0 END) AS qtd_encerrada,
               MAX(date(COALESCE(NULLIF(CAST(os.data_encerramento AS TEXT), ''), CAST(os.updated_at AS TEXT), CAST(os.created_at AS TEXT)))) AS ultima_data_encerramento
        FROM ordens_servico os JOIN ativos eq ON eq.id = os.equipamento_id LEFT JOIN ativos cp ON cp.id = os.componente_id
        WHERE UPPER(COALESCE(os.status,'')) IN ('ABERTA','EM EXECUÇÃO','EM EXECUCAO','ENCERRADA') {filtro_periodo}
        GROUP BY os.equipamento_id, os.componente_id, eq.tag, eq.descricao, cp.tag, cp.descricao
        HAVING COUNT(os.id) > 1 ORDER BY qtd_os_geral DESC, eq.tag ASC, cp.tag ASC LIMIT ?
    """, tuple(params) + (int(limit),))
    linhas=[]; total_retrabalho=0
    for row in cur.fetchall():
        item=dict(row); qtd=int(item.get('qtd_os_geral') or 0)
        item['qtd_os_encerradas']=qtd; item['qtd_reincidencias']=max(qtd-1,0)
        total_retrabalho += item['qtd_reincidencias']; linhas.append(item)
    cur.execute(f"SELECT COUNT(*) AS total_geral FROM ordens_servico os WHERE UPPER(COALESCE(os.status,'')) IN ('ABERTA','EM EXECUÇÃO','EM EXECUCAO','ENCERRADA') {filtro_periodo}", params)
    total=int(dict(cur.fetchone() or {}).get('total_geral') or 0)
    return {'total_retrabalho': total_retrabalho, 'itens_reincidentes': len(linhas), 'total_encerradas': total, 'percentual_os_retrabalho': round((total_retrabalho/total)*100,2) if total else 0.0, 'linhas': linhas}

def atualizar_os(os_id: str, alvo_ativo_id: str, descricao: str, tipo_os: Optional[str] = None, prioridade: Optional[str] = None, observacoes: Optional[str] = None, status: Optional[str] = None, justificativa_encerramento: Optional[str] = None, data_abertura: Optional[str] = None, unidade_medidor: Optional[str] = None, medidor_valor: Optional[float] = None, custo_terceiro: Optional[float] = None, descricao_servico_terceiro: Optional[str] = None, data_encerramento: Optional[str] = None, usuario_encerramento: Optional[str] = None, **kwargs):
    try:
        atual = _get_os_row(os_id)
        if not atual: raise ValueError('OS não encontrada.')
        descricao = _upper(descricao)
        if not descricao: raise ValueError('Informe a DESCRIÇÃO da OS.')
        alvo = _resolver_alvo_os(alvo_ativo_id); novo_status = _upper(status or atual['status'] or 'ABERTA')
        cols = _get_columns('ordens_servico')
        if novo_status == 'ENCERRADA':
            cur0 = _cursor(); cur0.execute("UPDATE os_atividades SET status = 'CONCLUÍDA', updated_at = ? WHERE os_id = ? AND UPPER(COALESCE(status,'')) <> 'CONCLUÍDA'", (_agora_sql(), os_id))
            data_final = _text(data_encerramento) or _agora_sql()
        else: data_final = None
        set_map = {'origem_tipo': alvo['origem_tipo'], 'equipamento_id': alvo['equipamento_id'], 'componente_id': alvo['componente_id'], 'descricao': descricao, 'tipo_os': _upper(tipo_os), 'prioridade': _upper(prioridade), 'observacoes': _text(observacoes), 'data_abertura': _text(data_abertura) or _text(atual.get('data_abertura')), 'unidade_medidor': _upper(unidade_medidor) or _upper(atual.get('unidade_medidor')) or 'HORÍMETRO', 'medidor_valor': float(medidor_valor) if medidor_valor not in ('', None) else atual.get('medidor_valor'), 'status': novo_status, 'justificativa_encerramento': _text(justificativa_encerramento), 'data_encerramento': data_final, 'updated_at': _agora_sql()}
        if 'custo_terceiro' in cols: set_map['custo_terceiro'] = float(custo_terceiro) if custo_terceiro not in ('', None) else float(atual.get('custo_terceiro') or 0)
        if 'descricao_servico_terceiro' in cols: set_map['descricao_servico_terceiro'] = _text(descricao_servico_terceiro)
        if 'usuario_encerramento' in cols and novo_status == 'ENCERRADA': set_map['usuario_encerramento'] = _text(usuario_encerramento)
        keys=[k for k in set_map if k in cols]; cur=_cursor(); cur.execute(f"UPDATE ordens_servico SET {', '.join([k+' = ?' for k in keys])} WHERE id = ?", tuple(set_map[k] for k in keys)+(os_id,))
        conn.commit(); return get_os(os_id)
    except Exception as ex:
        _rollback_safe(); registrar_erro_sistema('ATUALIZAR_OS', ex, {'os_id': os_id}); raise

def calcular_totais_os(os_id: str):
    cur = _cursor(); cur.execute('SELECT COALESCE(SUM(custo_total),0) AS v FROM os_materiais WHERE os_id = ?', (os_id,)); custo_materiais=float(dict(cur.fetchone() or {}).get('v') or 0)
    cur.execute('SELECT COALESCE(SUM(duracao_min),0) AS v, COALESCE(SUM(custo_hh),0) AS chh, COALESCE(SUM(custo_servico_terceiro),0) AS cst FROM os_atividades WHERE os_id = ?', (os_id,)); r=dict(cur.fetchone() or {})
    hh_min=float(r.get('v') or 0); custo_hh=float(r.get('chh') or 0); custo_terceiro=float(r.get('cst') or 0)
    try:
        cur.execute('SELECT COALESCE(custo_terceiro,0) AS extra FROM ordens_servico WHERE id = ?', (os_id,)); custo_terceiro += float(dict(cur.fetchone() or {}).get('extra') or 0)
    except Exception: pass
    return {'custo_materiais': custo_materiais, 'hh_min': hh_min, 'hh_horas': round(hh_min/60,2), 'custo_hh': custo_hh, 'custo_terceiro': custo_terceiro, 'custo_total_os': round(custo_materiais+custo_hh+custo_terceiro,2)}

try:
    _ensure_system_error_schema(); _ensure_gestao_dados_permissions()
except Exception as _edu_patch_ex:
    print(f'[DB][startup] patch Eduardo ignorado: {_edu_patch_ex}', flush=True)
# ===== FIM PATCH EDUARDO =====================================================

# ===== PATCH PERFIS CUSTOMIZÁVEIS EDUARDO ===================================
def _normalize_perfil_acesso(nome: str) -> str:
    valor = str(nome or '').strip().upper()
    mapa = {'GERENCIA': 'GESTOR', 'COMPLETO': 'GESTOR', 'TECNICO': 'EXECUTOR'}
    valor = mapa.get(valor, valor)
    if not valor:
        return 'VISUALIZACAO'
    try:
        cur = _cursor()
        cur.execute('SELECT nome FROM perfis_acesso WHERE UPPER(nome) = ? AND COALESCE(ativo,1) = 1', (valor,))
        if cur.fetchone():
            return valor
    except Exception:
        pass
    return valor if valor in PERFIS_ACESSO_PADRAO else 'VISUALIZACAO'

def ocultar_perfis_padrao_operacionais():
    """Deixa como perfil padrão visível apenas ADMIN e VISUALIZACAO; perfis novos continuam liberados."""
    try:
        cur = _cursor()
        cur.execute("UPDATE perfis_acesso SET ativo = 0 WHERE nome IN ('GESTOR','PLANEJADOR','EXECUTOR') AND nome NOT IN (SELECT DISTINCT UPPER(COALESCE(nivel_acesso,'')) FROM usuarios)")
        conn.commit()
    except Exception as ex:
        _rollback_safe()
        try: registrar_erro_sistema('OCULTAR_PERFIS_PADRAO', ex)
        except Exception: pass

try:
    ocultar_perfis_padrao_operacionais()
except Exception:
    pass
# ===== FIM PATCH PERFIS CUSTOMIZÁVEIS =======================================

# ===== HOTFIX 2026-04-30: DADOS / REVERSAO / EXPORT SEM LIMITE / EMAIL NAO BLOQUEANTE =====
def _ensure_data_import_schema():
    cur = _cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS data_import_batches (
            id TEXT PRIMARY KEY,
            tabela TEXT NOT NULL,
            modo TEXT NULL,
            linhas INTEGER NOT NULL DEFAULT 0,
            usuario_id TEXT NULL,
            revertido INTEGER NOT NULL DEFAULT 0,
            reverted_at TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS data_import_snapshots (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            tabela TEXT NOT NULL,
            registro_id TEXT NOT NULL,
            acao TEXT NOT NULL,
            old_json TEXT NULL,
            new_json TEXT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cur.execute('CREATE INDEX IF NOT EXISTS idx_data_import_snapshots_batch ON data_import_snapshots(batch_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_data_import_batches_created ON data_import_batches(created_at)')
    except Exception:
        pass
    conn.commit()


def listar_cargas_dados(limit: int = 30):
    try:
        _ensure_data_import_schema()
        cur = _cursor()
        cur.execute('SELECT * FROM data_import_batches ORDER BY created_at DESC LIMIT ?', (int(limit or 30),))
        return [dict(r) for r in cur.fetchall()]
    except Exception as ex:
        _rollback_safe()
        registrar_erro_sistema('LISTAR_CARGAS_DADOS', ex)
        return []


def listar_tabela_generica(tabela: str, limit: int | None = None):
    permitidas = {'ativos','ordens_servico','os_atividades','os_materiais','funcionarios','equipes','usuarios','audit_logs','system_error_logs'}
    tabela = str(tabela or '').strip()
    if tabela not in permitidas:
        raise ValueError('Tabela não liberada para exportação.')
    cur = _cursor()
    if limit in (None, '', 0, '0'):
        cur.execute(f'SELECT * FROM {tabela}')
    else:
        cur.execute(f'SELECT * FROM {tabela} LIMIT ?', (int(limit),))
    return [dict(r) for r in cur.fetchall()]


def importar_tabela_generica(tabela: str, linhas: list[dict], modo: str = 'upsert', usuario_id: str | None = None):
    permitidas = {'ativos','funcionarios','equipes'}
    tabela = str(tabela or '').strip()
    if tabela not in permitidas:
        raise ValueError('Importação liberada somente para ativos, funcionários e equipes.')
    cols_db = _get_columns(tabela)
    if not linhas:
        return {'linhas': 0, 'batch_id': None}
    _ensure_data_import_schema()
    batch_id = str(uuid.uuid4())
    cur = _cursor()
    count = 0
    cur.execute(
        'INSERT INTO data_import_batches (id, tabela, modo, linhas, usuario_id, created_at) VALUES (?, ?, ?, 0, ?, ?)',
        (batch_id, tabela, str(modo or 'upsert'), usuario_id, _agora_sql()),
    )
    try:
        for item in linhas:
            payload = {str(k).strip(): v for k, v in (item or {}).items() if str(k).strip() in cols_db and str(k).strip() not in {'created_at','updated_at'}}
            if not payload:
                continue
            if not payload.get('id'):
                payload['id'] = str(uuid.uuid4())
            registro_id = str(payload['id'])
            cur.execute(f'SELECT * FROM {tabela} WHERE id = ?', (registro_id,))
            old_row = cur.fetchone()
            old_dict = dict(old_row) if old_row else None
            acao = 'UPDATE' if old_dict else 'INSERT'
            if 'updated_at' in cols_db:
                payload['updated_at'] = _agora_sql()
            if 'created_at' in cols_db and not payload.get('created_at'):
                payload['created_at'] = _agora_sql()
            cols = list(payload.keys())
            vals = [payload[c] for c in cols]
            if modo == 'insert':
                cur.execute(f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})", tuple(vals))
            else:
                update_cols = [c for c in cols if c != 'id']
                cur.execute(
                    f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))}) "
                    f"ON CONFLICT (id) DO UPDATE SET {', '.join([c + ' = EXCLUDED.' + c for c in update_cols])}",
                    tuple(vals),
                )
            cur.execute(f'SELECT * FROM {tabela} WHERE id = ?', (registro_id,))
            new_row = cur.fetchone()
            cur.execute(
                'INSERT INTO data_import_snapshots (id, batch_id, tabela, registro_id, acao, old_json, new_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (str(uuid.uuid4()), batch_id, tabela, registro_id, acao, json.dumps(old_dict, ensure_ascii=False, default=str) if old_dict else None, json.dumps(dict(new_row), ensure_ascii=False, default=str) if new_row else None, _agora_sql()),
            )
            count += 1
        cur.execute('UPDATE data_import_batches SET linhas = ? WHERE id = ?', (count, batch_id))
        conn.commit()
        return {'linhas': count, 'batch_id': batch_id}
    except Exception:
        _rollback_safe()
        raise


def reverter_carga_dados(batch_id: str, usuario_id: str | None = None):
    batch_id = str(batch_id or '').strip()
    if not batch_id:
        raise ValueError('Lote inválido.')
    _ensure_data_import_schema()
    cur = _cursor()
    cur.execute('SELECT * FROM data_import_batches WHERE id = ?', (batch_id,))
    batch = cur.fetchone()
    if not batch:
        raise ValueError('Carga não encontrada.')
    batch = dict(batch)
    if int(batch.get('revertido') or 0):
        raise ValueError('Esta carga já foi revertida.')
    tabela = batch.get('tabela')
    if tabela not in {'ativos','funcionarios','equipes'}:
        raise ValueError('Tabela não liberada para reversão automática.')
    cur.execute('SELECT * FROM data_import_snapshots WHERE batch_id = ? ORDER BY created_at DESC', (batch_id,))
    snaps = [dict(r) for r in cur.fetchall()]
    cols_db = _get_columns(tabela)
    count = 0
    try:
        for snap in snaps:
            registro_id = snap.get('registro_id')
            old_json = snap.get('old_json')
            if not old_json:
                cur.execute(f'DELETE FROM {tabela} WHERE id = ?', (registro_id,))
            else:
                old = json.loads(old_json)
                payload = {k: v for k, v in old.items() if k in cols_db}
                cols = list(payload.keys())
                vals = [payload[c] for c in cols]
                update_cols = [c for c in cols if c != 'id']
                cur.execute(
                    f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))}) "
                    f"ON CONFLICT (id) DO UPDATE SET {', '.join([c + ' = EXCLUDED.' + c for c in update_cols])}",
                    tuple(vals),
                )
            count += 1
        cur.execute('UPDATE data_import_batches SET revertido = 1, reverted_at = ? WHERE id = ?', (_agora_sql(), batch_id))
        conn.commit()
        try:
            registrar_log_acao('REVERTER_CARGA_DADOS', 'DADOS', batch_id, {'tabela': tabela, 'linhas': count}, usuario_id=usuario_id)
        except Exception:
            pass
        return {'linhas': count, 'batch_id': batch_id}
    except Exception:
        _rollback_safe()
        raise


def _enviar_email_assincrono(destinatario: str, assunto: str, corpo_texto: str) -> bool:
    destinatario = str(destinatario or '').strip()
    if not destinatario or not _smtp_configurado():
        return False
    try:
        import threading
        def _job():
            try:
                _enviar_email(destinatario, assunto, corpo_texto)
            except Exception as ex:
                print(f'[EMAIL] falha ao enviar para {destinatario}: {ex}', flush=True)
        threading.Thread(target=_job, daemon=True).start()
        return True
    except Exception:
        return False


def enviar_link_redefinicao_usuario_email(nome: str, email: str, username: str, link: str, expires_at: str, motivo: str = 'definição de senha') -> bool:
    nome = str(nome or '').strip() or 'Usuário'
    corpo = f'''Olá, {nome}.

Recebemos uma solicitação de {motivo} para seu acesso ao Maintenance APP.

Usuário: {username}
Link seguro: {link}
Validade até: {expires_at}

Se você não reconhece esta ação, ignore este e-mail.
'''
    return _enviar_email_assincrono(email, 'Link de acesso - Maintenance APP', corpo)


def enviar_credenciais_usuario_email(nome: str, email: str, username: str, senha_temporaria: str) -> bool:
    nome = str(nome or '').strip() or 'Usuário'
    url = APP_BASE_URL or 'URL_DO_APP_NAO_CONFIGURADA'
    corpo = f'''Olá, {nome}.

Seu acesso ao Maintenance APP foi criado/atualizado.

Usuário: {username}
Senha temporária: {senha_temporaria}

No primeiro acesso, altere a senha imediatamente.
Link de acesso: {url}
'''
    return _enviar_email_assincrono(email, 'Credenciais de acesso - Maintenance APP', corpo)
