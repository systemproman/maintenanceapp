# Patch — Correção de rolagem externa e sidebar mobile

## Arquivos alterados

- `components/menu.py`
- `pages/arvore.py`
- `pages/dashboard.py`
- `pages/equipamentos.py`
- `pages/equipes.py`
- `pages/funcionarios.py`
- `pages/home.py`
- `pages/logs.py`
- `pages/os.py`
- `pages/usuarios.py`

## O que foi corrigido

1. Removida a barra de rolagem externa do app inteiro (`html`, `body`, `#app`, `.nicegui-content`).
2. Mantida rolagem apenas nas áreas internas que já usam `overflow-auto`.
3. Adicionada a classe global `fsl-app-shell` nos wrappers principais das páginas.
4. Adicionada a classe `fsl-sidebar` na barra lateral.
5. Corrigido o comportamento de altura no iPhone/rotação usando `100dvh` e fallback para `-webkit-fill-available`.
6. Melhorado o modo landscape/mobile para a sidebar não ficar com fundo cortado.

## Como aplicar

Copie os arquivos deste patch por cima dos arquivos atuais do projeto.

Depois rode:

```bash
git add .
git commit -m "fix global scroll and mobile sidebar layout"
git push
```

No Render, faça Manual Deploy.

## Observação

Se o iPhone continuar exibindo visual antigo, remova o PWA da tela inicial e limpe o cache do Safari antes de testar novamente.
