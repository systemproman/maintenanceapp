from nicegui import app

def get_user():
    return app.storage.user.get('user', {})

def get_role():
    return str(app.storage.user.get('role') or 'VISUALIZACAO').upper()

def is_read_only():
    return get_role() == 'VISUALIZACAO'
