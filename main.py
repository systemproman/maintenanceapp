import os
import mimetypes
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from nicegui import app, ui

from auth import build_login_page, build_change_password_page, build_reset_password_page, require_auth, needs_password_change, can_access_route
from pages.arvore import arvore_page
from pages.equipamentos import equipamentos_page
from pages.home import build_home_page
from pages.os import os_page
from pages.equipes import equipes_page
from pages.funcionarios import funcionarios_page
from pages.usuarios import usuarios_page
from pages.dashboard import dashboard_page
from pages.logs import logs_page
from services.db import close_connection, get_anexo, get_connection
from keepalive import setup_keepalive
from pwa import inject_pwa_head, setup_global_pwa


Path('assets').mkdir(parents=True, exist_ok=True)
Path('uploads').mkdir(parents=True, exist_ok=True)

app.add_static_files('/assets', 'assets')
app.add_static_files('/uploads', 'uploads')

setup_global_pwa()


def _resolver_anexo(anexo_id: str):
    anexo = get_anexo(anexo_id)
    if not anexo:
        raise HTTPException(status_code=404, detail='Anexo não encontrado.')
    caminho = Path(str(anexo.get('caminho') or ''))
    if not caminho.exists() or not caminho.is_file():
        raise HTTPException(status_code=404, detail='Arquivo físico não encontrado.')
    return anexo, caminho


@app.get('/arquivo/{anexo_id}/{nome}')
def abrir_arquivo(anexo_id: str, nome: str):
    anexo, caminho = _resolver_anexo(anexo_id)
    filename = str(anexo.get('nome_original') or anexo.get('nome_salvo') or unquote(nome) or caminho.name)
    media_type, _ = mimetypes.guess_type(str(caminho))
    if not media_type:
        media_type = 'application/octet-stream'
    return FileResponse(path=str(caminho), filename=filename, media_type=media_type, headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.get('/download/{anexo_id}/{nome}')
def baixar_arquivo(anexo_id: str, nome: str):
    anexo, caminho = _resolver_anexo(anexo_id)
    filename = str(anexo.get('nome_original') or anexo.get('nome_salvo') or unquote(nome) or caminho.name)
    return FileResponse(path=str(caminho), filename=filename, media_type='application/octet-stream', content_disposition_type='attachment')


@app.on_shutdown
def _close_db_on_shutdown():
    try:
        close_connection()
        print('🛑 conexão com banco encerrada.', flush=True)
    except Exception as e:
        print(f'⚠️ falha ao encerrar conexão do banco: {e}', flush=True)


@app.get('/ping')
def ping():
    try:
        db = get_connection()
        cur = db.cursor()
        cur.execute('SELECT 1')
        cur.fetchone()
        return {'status': 'ok', 'db': 'ok'}
    except Exception as e:
        return JSONResponse(status_code=500, content={'status': 'erro', 'erro': str(e)})


def _protect_page(body_fn, route: str = '/home'):
    inject_pwa_head()
    if not require_auth():
        ui.navigate.to('/')
        return
    if needs_password_change():
        ui.navigate.to('/trocar-senha')
        return
    if route and not can_access_route(route):
        ui.notify('Você não possui permissão para acessar esta tela.', type='negative')
        ui.navigate.to('/home')
        return
    body_fn()


@ui.page('/')
def login_page():
    inject_pwa_head()
    build_login_page()


@ui.page('/trocar-senha')
def change_password_page():
    inject_pwa_head()
    if not require_auth():
        ui.navigate.to('/')
        return
    build_change_password_page()


@ui.page('/definir-senha')
def reset_password_page(token: str = ''):
    inject_pwa_head()
    build_reset_password_page(token)


@ui.page('/home')
def home_page():
    _protect_page(build_home_page, '/home')


@ui.page('/arvore')
def page_arvore():
    _protect_page(arvore_page, '/arvore')


@ui.page('/equipamentos')
def page_equipamentos():
    _protect_page(equipamentos_page, '/equipamentos')


@ui.page('/ativos')
def page_ativos():
    _protect_page(equipamentos_page, '/equipamentos')


@ui.page('/os')
def page_os():
    _protect_page(os_page, '/os')


@ui.page('/equipes')
def page_equipes():
    _protect_page(equipes_page, '/equipes')


@ui.page('/funcionarios')
def page_funcionarios():
    _protect_page(funcionarios_page, '/funcionarios')


@ui.page('/usuarios')
def page_usuarios():
    _protect_page(usuarios_page, '/usuarios')


@ui.page('/dashboard')
def page_dashboard():
    _protect_page(dashboard_page, '/dashboard')


@ui.page('/logs')
def page_logs():
    _protect_page(logs_page, '/logs')


PORT = int(os.environ.get('PORT', 8080))
HOST = '0.0.0.0'

print(f'🚀 INICIANDO APP EM {HOST}:{PORT}')

setup_keepalive()                                              # ← ADICIONADO

ui.run(
    title='Maintenance APP',
    host=HOST,
    port=PORT,
    favicon='assets/logo_fsl.png',
    storage_secret=os.environ.get('STORAGE_SECRET', 'cmms_login_secret_2026'),
    reload=False,
    language='pt-BR',
    show=False,
)
