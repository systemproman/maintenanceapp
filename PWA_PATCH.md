# Patch PWA Global — Maintenance APP

## O que foi alterado

- Adicionado `pwa.py` com:
  - `/manifest.webmanifest`
  - `/manifest.json`
  - `/sw.js`
  - registro global do Service Worker
- Ajustado `main.py` para injetar PWA em todas as páginas principais:
  - `/`
  - `/home`
  - `/arvore`
  - `/equipamentos`
  - `/ativos`
  - `/os`
  - `/equipes`
  - `/funcionarios`
  - `/usuarios`
  - `/dashboard`
  - `/logs`
  - `/trocar-senha`
  - `/definir-senha`
- Adicionados ícones:
  - `assets/pwa-icon-192.png`
  - `assets/pwa-icon-512.png`

## Como validar depois do deploy

Abra no navegador:

```text
https://maintenanceapp-8epo.onrender.com/manifest.webmanifest
https://maintenanceapp-8epo.onrender.com/sw.js
```

Se os dois abrirem, o PWA está publicado.

Depois abra o app normalmente e use:

- Chrome/Edge PC: botão de instalar na barra de endereço ou menu ⋮ > Instalar app.
- Android: menu ⋮ > Adicionar à tela inicial / Instalar app.

## Render

Depois de subir o patch:

```bash
git add .
git commit -m "add global pwa support"
git push
```

No Render, faça novo deploy. Se o navegador ainda não mostrar instalação, limpe o cache do site ou abra em aba anônima.
