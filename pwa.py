"""
pwa.py — PWA global da aplicação Maintenance APP.
Registra manifest e service worker para a aplicação inteira.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse, Response
from nicegui import app, ui

APP_NAME = 'Maintenance APP'
APP_SHORT_NAME = 'Maintenance'
THEME_COLOR = '#0f172a'
BACKGROUND_COLOR = '#0b1020'


def inject_pwa_head() -> None:
    ui.add_head_html(f'''
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="{THEME_COLOR}">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{APP_SHORT_NAME}">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="apple-touch-icon" href="/assets/pwa-icon-192.png">
''')
    ui.add_body_html('''
<script>
(function () {
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .catch(function (err) { console.warn('Falha ao registrar Service Worker:', err); });
  });
})();
</script>
''')


def _manifest() -> dict:
    return {
        'name': APP_NAME,
        'short_name': APP_SHORT_NAME,
        'description': 'Sistema de manutenção e gestão operacional.',
        'id': '/',
        'start_url': '/home',
        'scope': '/',
        'display': 'standalone',
        'orientation': 'any',
        'background_color': BACKGROUND_COLOR,
        'theme_color': THEME_COLOR,
        'categories': ['business', 'productivity', 'utilities'],
        'icons': [
            {'src': '/assets/pwa-icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': '/assets/pwa-icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
        'shortcuts': [
            {'name': 'Home', 'short_name': 'Home', 'url': '/home'},
            {'name': 'Ordens de Serviço', 'short_name': 'OS', 'url': '/os'},
            {'name': 'Árvore de Equipamentos', 'short_name': 'Árvore', 'url': '/arvore'},
        ],
    }


_SW_JS = r'''
const CACHE_NAME = 'maintenance-app-v1';
const APP_SHELL = [
  '/',
  '/home',
  '/manifest.webmanifest',
  '/assets/pwa-icon-192.png',
  '/assets/pwa-icon-512.png',
  '/assets/logo_fsl.png',
  '/assets/logo_app.png'
];

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL).catch(() => null))
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (url.pathname.startsWith('/_nicegui') || url.pathname.startsWith('/socket.io')) return;

  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
        return res;
      }).catch(() => caches.match(req).then(cached => cached || caches.match('/')))
    );
    return;
  }

  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(req).then(cached => cached || fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
        return res;
      }))
    );
    return;
  }

  event.respondWith(
    fetch(req).then(res => {
      if (res && res.status === 200) {
        const copy = res.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
      }
      return res;
    }).catch(() => caches.match(req))
  );
});
'''


def setup_global_pwa() -> None:
    @app.get('/manifest.webmanifest')
    def manifest_webmanifest():
        return JSONResponse(_manifest(), media_type='application/manifest+json')

    @app.get('/manifest.json')
    def manifest_json():
        return JSONResponse(_manifest())

    @app.get('/sw.js')
    def service_worker():
        return Response(
            content=_SW_JS,
            media_type='application/javascript',
            headers={'Service-Worker-Allowed': '/', 'Cache-Control': 'no-cache'},
        )
