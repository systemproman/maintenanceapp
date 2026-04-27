from nicegui import ui, app
from components.menu import build_menu, show_page_loader, hide_page_loader


def build_home_page():
    nome = str(app.storage.user.get('name') or 'USUÁRIO').upper()

    ui.add_head_html('''
    <style>
        .home-main-area {
            position: relative;
            flex: 1 1 auto;
            height: 100vh;
            overflow: hidden;
            background: #0f172a url('/assets/fundo_home.png') center center / cover no-repeat;
        }
        .home-slide {
            position: absolute;
            inset: 0;
            background-position: center center;
            background-size: cover;
            background-repeat: no-repeat;
            opacity: 0;
            transform: scale(1.02);
            will-change: opacity, transform;
        }
        .home-slide-1 {
            background-image: url('/assets/bg1.jpg');
            animation: homeFade1 18s infinite;
        }
        .home-slide-2 {
            background-image: url('/assets/bg2.jpg');
            animation: homeFade2 18s infinite;
        }
        .home-slide-3 {
            background-image: url('/assets/bg3.jpg');
            animation: homeFade3 18s infinite;
        }
        @keyframes homeFade1 {
            0%, 28% { opacity: 1; transform: scale(1.02); }
            33%, 100% { opacity: 0; transform: scale(1.06); }
        }
        @keyframes homeFade2 {
            0%, 28% { opacity: 0; transform: scale(1.06); }
            33%, 61% { opacity: 1; transform: scale(1.02); }
            66%, 100% { opacity: 0; transform: scale(1.06); }
        }
        @keyframes homeFade3 {
            0%, 61% { opacity: 0; transform: scale(1.06); }
            66%, 94% { opacity: 1; transform: scale(1.02); }
            100% { opacity: 0; transform: scale(1.06); }
        }
        .home-overlay {
            position: absolute;
            inset: 0;
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.16), rgba(15, 23, 42, 0.56));
        }
        .home-content {
            position: relative;
            z-index: 2;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            padding: 28px;
            box-sizing: border-box;
        }
        .home-brand {
            display: inline-flex;
            align-items: center;
            gap: 12px;
            padding: 10px 16px;
            border-radius: 16px;
            width: fit-content;
            background: rgba(15, 23, 42, 0.22);
            backdrop-filter: blur(4px);
            box-shadow: 0 8px 28px rgba(0,0,0,0.18);
            margin-bottom: 18px;
        }
        .home-brand-title {
            color: #ffffff;
            font-size: 24px;
            font-weight: 800;
            letter-spacing: 0.4px;
            line-height: 1;
            text-shadow: 0 2px 18px rgba(0,0,0,0.35);
        }
        .home-title {
            color: white;
            font-size: 34px;
            font-weight: 800;
            line-height: 1.05;
            text-shadow: 0 2px 18px rgba(0,0,0,0.35);
        }
        .home-subtitle {
            color: rgba(255,255,255,0.90);
            font-size: 15px;
            font-weight: 600;
            margin-top: 6px;
            text-shadow: 0 2px 12px rgba(0,0,0,0.30);
        }
        .home-logo {
            width: 44px;
            height: 44px;
            object-fit: contain;
            filter: drop-shadow(0 2px 10px rgba(0,0,0,0.25));
        }
    </style>
    ''')

    with ui.row().classes('fsl-app-shell w-full h-screen no-wrap bg-slate-100'):
        build_menu()
        with ui.element('div').classes('home-main-area'):
            ui.element('div').classes('home-slide home-slide-1')
            ui.element('div').classes('home-slide home-slide-2')
            ui.element('div').classes('home-slide home-slide-3')
            ui.element('div').classes('home-overlay')
            with ui.element('div').classes('home-content'):
                with ui.row().classes('items-center no-wrap home-brand'):
                    ui.image('/assets/logo_app.png').classes('home-logo')
                    ui.label('MAINTENANCE APP').classes('home-brand-title')
                ui.label(f'BEM-VINDO, {nome}').classes('home-title')
                ui.label('GESTÃO DE MANUTENÇÃO, ATIVOS, OS E INDICADORES').classes('home-subtitle')
    ui.timer(0.25, hide_page_loader, once=True)
