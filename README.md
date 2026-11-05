# NEXO REDES — MVP de desenvolvimento

Esta primeira versão é um protótipo local para validar a interface e integrar o motor Python + Gemini extraído do notebook TCC2.

## Executar localmente

1. Instale Python 3.11 ou superior.
2. Crie e ative um ambiente virtual.
3. Execute `pip install -r requirements.txt`.
4. Configure a variável de ambiente `GEMINI_API_KEY` ou crie `.streamlit/secrets.toml` a partir do exemplo.
5. Execute `streamlit run app.py`.

## Atenção

- Esta versão ainda NÃO possui autenticação, banco de dados, armazenamento persistente nem separação segura entre clientes e analistas.
- Não publique para clientes nem use documentos pessoais/reais antes da implementação de autenticação, políticas de acesso (RLS), armazenamento privado e auditoria.
- As regras de negócio devem ser homologadas com a versão oficial vigente da NT.017.EQTL e com os responsáveis da distribuidora antes de uso operacional.
- A planilha do Anexo VII foi tratada como recomendada, conforme checklist operacional informado pela responsável pelo projeto.
- A declaração de iluminação pública é exigida pelo protótipo somente quando a obra é identificada como loteamento de lotes de terreno.

## Próxima etapa

Implementar Supabase Auth, perfis `cliente` e `analista`, tabelas de obras/submissões, Storage privado, políticas RLS e histórico de auditoria. Só depois publicar o portal online.
