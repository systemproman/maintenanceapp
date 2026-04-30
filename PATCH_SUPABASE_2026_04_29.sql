-- PATCH EDUARDO 2026-04-29
-- Rode no SQL Editor do Supabase se quiser criar as estruturas antes do deploy.
-- O app também tenta criar automaticamente no startup.

BEGIN;

SET TIME ZONE 'America/Campo_Grande';

CREATE TABLE IF NOT EXISTS system_error_logs (
    id TEXT PRIMARY KEY,
    origem TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    traceback TEXT NULL,
    contexto_json TEXT NULL,
    usuario_id TEXT NULL,
    resolvido INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_system_error_logs_created_at
    ON system_error_logs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_system_error_logs_origem
    ON system_error_logs(origem);

INSERT INTO perfil_permissoes (
    id, perfil_nome, modulo,
    ver_menu, abrir_tela, criar, editar, excluir, exportar,
    aprovar_liberar, ver_logs, gerenciar_usuarios, gerenciar_permissoes,
    created_at, updated_at
)
SELECT gen_random_uuid()::text, 'ADMIN', 'GESTAO_DADOS',
       1, 1, 1, 1, 1, 1,
       1, 1, 1, 1,
       NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM perfil_permissoes
    WHERE perfil_nome = 'ADMIN' AND modulo = 'GESTAO_DADOS'
);

INSERT INTO perfil_permissoes (
    id, perfil_nome, modulo,
    ver_menu, abrir_tela, criar, editar, excluir, exportar,
    aprovar_liberar, ver_logs, gerenciar_usuarios, gerenciar_permissoes,
    created_at, updated_at
)
SELECT gen_random_uuid()::text, 'VISUALIZACAO', 'GESTAO_DADOS',
       0, 0, 0, 0, 0, 0,
       0, 0, 0, 0,
       NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM perfil_permissoes
    WHERE perfil_nome = 'VISUALIZACAO' AND modulo = 'GESTAO_DADOS'
);

-- Oculta perfis operacionais padrão sem apagar, se não houver usuário vinculado.
UPDATE perfis_acesso
SET ativo = 0, updated_at = NOW()
WHERE nome IN ('GESTOR', 'PLANEJADOR', 'EXECUTOR')
  AND nome NOT IN (
      SELECT DISTINCT UPPER(COALESCE(nivel_acesso, ''))
      FROM usuarios
  );

COMMIT;
