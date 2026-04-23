# Deploy no Render

## O que foi corrigido

**Problema principal:** o `settings.py` carregava o `.env` com caminho relativo (`".env"`), 
que só funciona se o processo rodar exatamente da pasta do app. No Render isso nunca acontecia, 
então `DATABASE_URL` ficava vazio e o banco não conectava.

**Correção:** o caminho agora é absoluto baseado em `__file__`, funcionando independente 
do diretório de trabalho. Além disso, `override=False` garante que variáveis de ambiente 
do sistema (configuradas no Render) tenham prioridade sobre o `.env`.

---

## Como fazer o deploy

### 1. Suba o código para um repositório Git (GitHub/GitLab)
```bash
git init
git add .
git commit -m "primeiro commit"
git remote add origin https://github.com/seu-usuario/seu-repo.git
git push -u origin main
```

### 2. No Render (render.com)
1. Clique em **New > Web Service**
2. Conecte seu repositório Git
3. O Render vai detectar o `Dockerfile` automaticamente

### 3. Configure as variáveis de ambiente no painel do Render
As variáveis marcadas com `sync: false` no `render.yaml` precisam ser 
preenchidas manualmente no painel (**Environment > Add Environment Variable**):

| Variável | Valor |
|---|---|
| `DATABASE_URL` | `postgresql://postgres:DBMainApp2026@db.xxx.supabase.co:5432/postgres` |
| `SUPABASE_URL` | `https://xxx.supabase.co` |
| `SUPABASE_KEY` | chave anon do Supabase |
| `SUPABASE_SERVICE_KEY` | chave service_role do Supabase |
| `STORAGE_SECRET` | sua senha secreta |
| `SMTP_USER` | email do Gmail |
| `SMTP_PASSWORD` | app password do Gmail |
| `SMTP_FROM_EMAIL` | email do Gmail |
| `APP_BASE_URL` | `https://seu-app.onrender.com` |

> ⚠️ **Não commite o `.env` com as credenciais reais para o Git!**  
> Adicione `.env` ao `.gitignore`.

### 4. Deploy
Clique em **Deploy** — o Render vai buildar a imagem Docker e subir o app.

---

## Testando se o banco conectou
Após o deploy, acesse:
```
https://seu-app.onrender.com/ping
```
Resposta esperada: `{"status": "ok", "db": "ok"}`
