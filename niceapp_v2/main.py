import os
import mimetypes
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from nicegui import app, ui

from auth import build_login_page, build_change_password_page, build_reset_password_page, require_auth, needs_password_change
from pages.arvore import arvore_page
from pages.equipamentos import equipamentos_page
from pages.home import build_home_page
from pages.os import os_page
from pages.equipes import equipes_page
from pages.funcionarios import funcionarios_page
from pages.usuarios import usuarios_page
from pages.dashboard import dashboard_page
from pages.logs import logs_page
from services.db import get_anexo, get_connection


Path('assets').mkdir(parents=True, exist_ok=True)
Path('uploads').mkdir(parents=True, exist_ok=True)

app.add_static_files('/assets', 'assets')
app.add_static_files('/uploads', 'uploads')


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


def _protect_page(body_fn):
    if not require_auth():
        ui.navigate.to('/')
        return
    if needs_password_change():
        ui.navigate.to('/trocar-senha')
        return
    body_fn()


@ui.page('/')
def login_page():
    build_login_page()


@ui.page('/trocar-senha')
def change_password_page():
    if not require_auth():
        ui.navigate.to('/')
        return
    build_change_password_page()

@ui.page('/definir-senha')
def reset_password_page(token: str = ''):
    build_reset_password_page(token)


@ui.page('/home')
def home_page():
    _protect_page(build_home_page)


@ui.page('/arvore')
def page_arvore():
    _protect_page(arvore_page)


@ui.page('/equipamentos')
def page_equipamentos():
    _protect_page(equipamentos_page)


@ui.page('/ativos')
def page_ativos():
    _protect_page(equipamentos_page)


@ui.page('/os')
def page_os():
    _protect_page(os_page)


@ui.page('/equipes')
def page_equipes():
    _protect_page(equipes_page)


@ui.page('/funcionarios')
def page_funcionarios():
    _protect_page(funcionarios_page)


@ui.page('/usuarios')
def page_usuarios():
    _protect_page(usuarios_page)


@ui.page('/dashboard')
def page_dashboard():
    _protect_page(dashboard_page)


@ui.page('/logs')
def page_logs():
    _protect_page(logs_page)


PORT = int(os.environ.get('PORT', 8080))
HOST = '0.0.0.0'

print(f'🚀 INICIANDO APP EM {HOST}:{PORT}')

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
