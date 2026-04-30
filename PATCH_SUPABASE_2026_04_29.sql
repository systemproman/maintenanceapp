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

-- ===== HOTFIX 2026-04-30: LOTES DE CARGA / REVERSAO =====
CREATE TABLE IF NOT EXISTS data_import_batches (
    id TEXT PRIMARY KEY,
    tabela TEXT NOT NULL,
    modo TEXT NULL,
    linhas INTEGER NOT NULL DEFAULT 0,
    usuario_id TEXT NULL,
    revertido INTEGER NOT NULL DEFAULT 0,
    reverted_at TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_import_snapshots (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    tabela TEXT NOT NULL,
    registro_id TEXT NOT NULL,
    acao TEXT NOT NULL,
    old_json TEXT NULL,
    new_json TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_data_import_batches_created ON data_import_batches(created_at);
CREATE INDEX IF NOT EXISTS idx_data_import_snapshots_batch ON data_import_snapshots(batch_id);
