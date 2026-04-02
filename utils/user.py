
from nicegui import app

def get_user():
    return app.storage.user.get('user', {})

def get_role():
    user = get_user()
    return (user.get('nivel_acesso') or 'VISUALIZACAO').upper()

def is_read_only():
    return get_role() != 'COMPLETO'
