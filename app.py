import os, re, json, random, tempfile, shutil
from datetime import datetime
import pandas as pd
import streamlit as st
from supabase import create_client

if "GEMINI_API_KEY" in st.secrets:
    os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]

from pipeline import (
    SolicitacaoIncorporacao, validar_documentacao,
    analisar_pasta_pdfs, MODELO_DECISAO, MODELO_EXTRACAO_PDF,
)

st.set_page_config(page_title="Validação Documental NT.017", layout="wide")

@st.cache_resource
def db():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

TIPOS = {
    "Incorporação (com ressarcimento)": "incorporacao_rede",
    "Doação — Rede/Transformador NOVO": "doacao_rede_nova",
    "Doação — Rede/Transformador EXISTENTE": "doacao_rede_existente",
}

# Pergunta-chave: obra com TR até 300 kVA é ISENTA de Projeto Elétrico (normativa da distribuidora)
TR_OPCOES = {
    "Não sei (a IA tenta identificar)": None,
    "Não (até 300 kVA)": False,
    "Sim (acima de 300 kVA)": True,
}

# ---------------- ÁREA DO ADMINISTRADOR (barra lateral) ----------------
with st.sidebar:
    st.header("Administração")
    senha = st.text_input("Senha", type="password")
    admin = bool(senha) and senha == st.secrets.get("ADMIN_SENHA", None)

if admin:
    st.title("📋 Validação final do analista")

    dados = db().table("submissoes").select("*").order("criado_em", desc=True).execute().data
    if not dados:
        st.info("Nenhuma obra enviada ainda.")
        st.stop()

    def _rotulo(d):
        situacao = "✅ DEFINITIVO" if d.get("validado_em") else "⏳ aguardando validação"
        return f"{d['protocolo']} | {d['responsavel_email']} | IA: {d['status']} | {situacao}"

    idx = st.selectbox("Selecione a obra", list(range(len(dados))),
                       format_func=lambda i: _rotulo(dados[i]))
    escolhida = dados[idx]
    r = escolhida.get("resultado") or {}

    # ---- resultado proposto pela IA ----
    st.subheader("Proposta da IA")
    st.warning(f"{escolhida['status']}  |  Conformidade: {escolhida.get('score')}/100  "
               f"(resultado PRELIMINAR até a validação do analista)")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Documentos reconhecidos**")
        for x in r.get("documentos_reconhecidos", []):
            st.write("✓", x)
        st.markdown("**Documentos faltantes**")
        for x in r.get("documentos_faltantes", []):
            st.write("✗", x)
    with c2:
        st.markdown("**Pendências e alertas**")
        for x in list(r.get("pendencias_condicionais", [])) + list(r.get("alertas_ativos", [])):
            st.write("⚠️", x)
        st.markdown("**Recomendações**")
        for x in r.get("recomendacoes", []):
            st.write("→", x)
    st.markdown(f"**Justificativa da IA:** {r.get('justificativa_tecnica', '—')}")

    # ---- validação humana (etapa final obrigatória) ----
    st.subheader("Decisão do analista")
    if escolhida.get("validado_em"):
        st.success(f"✅ DEFINITIVO: {escolhida.get('decisao_final')}  "
                   f"(ação: {escolhida.get('acao_analista')}, analista: {escolhida.get('analista')}, "
                   f"em {str(escolhida['validado_em'])[:16].replace('T', ' ')})")
        if escolhida.get("divergiu_da_ia"):
            st.info("O analista divergiu da proposta da IA.")
        if escolhida.get("observacao"):
            st.write("Observação:", escolhida["observacao"])
    else:
        with st.form(f"validacao_{idx}"):
            analista = st.text_input("Nome do analista *")
            acao = st.radio("Decisão", ["Concordo com a IA", "Altero para…", "Rejeito (reprovo)"])
            novo = st.selectbox("Se for alterar, status final:",
                                ["APROVADO", "APROVADO_COM_RESSALVAS", "REPROVADO"])
            obs = st.text_area("Observação (obrigatória se alterar ou rejeitar)")
            salvar = st.form_submit_button("Registrar decisão final", type="primary")

        if salvar:
            if not analista.strip():
                st.error("Informe o nome do analista.")
            elif acao != "Concordo com a IA" and not obs.strip():
                st.error("Explique o motivo na observação.")
            else:
                if acao == "Concordo com a IA":
                    final, tag = escolhida["status"], "CONCORDO"
                elif acao == "Altero para…":
                    final, tag = novo, "ALTERO"
                else:
                    final, tag = "REPROVADO", "REJEITO"

                try:
                    q = db().table("submissoes").update({
                        "decisao_ia": escolhida["status"],
                        "decisao_final": final,
                        "acao_analista": tag,
                        "analista": analista.strip(),
                        "observacao": obs.strip(),
                        "divergiu_da_ia": final != escolhida["status"],
                        "validado_em": datetime.now().astimezone().isoformat(),
                    })
                    if escolhida.get("id") is not None:
                        q = q.eq("id", escolhida["id"])
                    else:
                        q = q.eq("protocolo", escolhida["protocolo"]) \
                             .eq("responsavel_email", escolhida["responsavel_email"])
                    q.execute()
                    st.success("Decisão registrada.")
                    st.rerun()
                except Exception as e:
                    if "rerun" in type(e).__name__.lower():
                        raise
                    st.error(f"Não foi possível salvar a decisão (as colunas novas foram criadas "
                             f"no Supabase?): {e}")

    # ---- visão geral ----
    st.subheader("Todas as obras")
    cols = ["criado_em", "protocolo", "responsavel_email", "tipo", "status",
            "decisao_final", "analista", "divergiu_da_ia", "validado_em"]
    st.dataframe(pd.DataFrame(dados).reindex(columns=cols), use_container_width=True)

    st.subheader("Responsáveis cadastrados")
    rts = db().table("responsaveis").select("*").order("criado_em", desc=True).execute().data
    st.dataframe(pd.DataFrame(rts), use_container_width=True)
    st.stop()

# ---------------- TELA 1: CADASTRO ----------------
st.title("🔌 Validação Documental — Incorporação/Doação de Rede")
st.caption("CEEE-D / Equatorial Energia | NT.017.EQTL – Revisão 04")

if "rt" not in st.session_state:
    st.subheader("1. Cadastro do responsável técnico")
    with st.form("cadastro"):
        nome = st.text_input("Nome completo *")
        crea = st.text_input("CREA / CFT")
        email = st.text_input("E-mail *")
        telefone = st.text_input("Telefone")
        empresa = st.text_input("Empresa")
        ok = st.form_submit_button("Cadastrar e continuar")
    if ok:
        if not nome.strip() or "@" not in email:
            st.error("Preencha o nome e um e-mail válido.")
        else:
            rt = {"nome": nome.strip(), "crea": crea.strip(), "email": email.strip().lower(),
                  "telefone": telefone.strip(), "empresa": empresa.strip()}
            db().table("responsaveis").upsert(rt, on_conflict="email").execute()
            st.session_state["rt"] = rt
            st.rerun()
    st.stop()

rt = st.session_state["rt"]
st.success(f"Olá, {rt['nome']}!")

# ---------------- TELA 2: ENVIO ----------------
st.subheader("2. Envio da documentação da obra")
col1, col2 = st.columns(2)
with col1:
    tipo = TIPOS[st.radio("Tipo de obra", list(TIPOS))]
    protocolo = st.text_input(
        "Protocolo", f"{datetime.now().year}-{random.randint(100000, 999999)}"
    )
with col2:
    tr_acima_300 = TR_OPCOES[st.radio(
        "A obra possui transformador (TR) maior que 300 kVA?",
        list(TR_OPCOES),
        horizontal=True,
        help="Obras com TR de até 300 kVA são isentas de Projeto Elétrico. "
             "Se marcar 'Não', o projeto não será cobrado. Se marcar 'Sim', será exigido normalmente.",
    )]
    restituicao = st.checkbox(
        "Há restituição de valores ao cliente? (só em incorporação)",
        disabled=(tipo != "incorporacao_rede"),
    )
    isencao = st.checkbox(
        "Confirmo que este pedido NÃO é acesso a geração distribuída, "
        "NÃO é migração para mercado livre e NÃO tem situação omissa à "
        "Norma Técnica (só relevante se a subestação for ≤300kVA)",
        disabled=(tr_acima_300 is True),
    )

arquivos = st.file_uploader("Anexe os PDFs da obra", type="pdf", accept_multiple_files=True)

if st.button("▶ Processar documentação", type="primary", disabled=not arquivos):
    pasta = tempfile.mkdtemp()
    try:
        for f in arquivos:
            with open(os.path.join(pasta, f.name), "wb") as out:
                out.write(f.getbuffer())

        with st.spinner("Lendo os PDFs (pode levar alguns minutos)..."):
            ext = analisar_pasta_pdfs(pasta, tipo, modelo=MODELO_EXTRACAO_PDF)

        info = ext["info_geral"]
        sol = SolicitacaoIncorporacao(
            numero_protocolo=protocolo,
            tipo_solicitacao=tipo,
            nome_responsavel=info["nome_responsavel"] or "Nome do Responsável (não encontrado nos PDFs)",
            cpf_cnpj=info["cpf_cnpj"] or "000.000.000-00 (não encontrado nos PDFs)",
            endereco_obra=info["endereco_obra"] or "Endereço não encontrado nos PDFs",
            descricao_obra=info["descricao_obra"] or "Descrição não encontrada nos PDFs",
            data_aprovacao_projeto=ext["data_aprovacao_projeto"],
            data_aprovacao_orcamento=ext["data_aprovacao_orcamento"],
            possui_restituicao_valores=(restituicao and tipo == "incorporacao_rede"),
            documentos_apresentados=ext["documentos_apresentados"],
            potencia_transformador_kva=ext.get("potencia_transformador_kva"),
            tr_acima_300kva=tr_acima_300,
            condicoes_isencao_confirmadas=(True if (isencao and tr_acima_300 is not True) else None),
        )

        with st.spinner("Validando conforme a NT.017..."):
            res = validar_documentacao(sol, modelo=MODELO_DECISAO)

        # guarda PDFs e resultado no Supabase
        seguro = re.sub(r"[^a-zA-Z0-9_.-]", "_", rt["email"])
        proto_seg = re.sub(r"[^a-zA-Z0-9_.-]", "_", protocolo)
        caminhos = []
        for f in arquivos:
            nome_seg = re.sub(r"[^a-zA-Z0-9_.-]", "_", f.name)
            caminho = f"{seguro}/{proto_seg}/{nome_seg}"
            db().storage.from_("pdfs").upload(
                caminho, f.getvalue(),
                {"content-type": "application/pdf", "upsert": "true"},
            )
            caminhos.append(caminho)

        db().table("submissoes").insert({
            "responsavel_email": rt["email"], "protocolo": protocolo, "tipo": tipo,
            "status": res.status, "score": res.score_conformidade,
            "resultado": json.loads(json.dumps(res.para_dict(), default=str)),
            "arquivos": caminhos,
        }).execute()

        st.session_state["resultado"] = res
        st.session_state["extracao"] = ext
    except Exception as e:
        st.error(f"Erro no processamento: {e}")
    finally:
        shutil.rmtree(pasta, ignore_errors=True)

# ---------------- TELA 3: RESULTADO ----------------
res = st.session_state.get("resultado")
if res:
    ext = st.session_state["extracao"]
    mostrar = {"APROVADO": st.success, "REPROVADO": st.error,
               "APROVADO_COM_RESSALVAS": st.warning}.get(res.status, st.info)
    mostrar(f"{res.status}  |  Conformidade: {res.score_conformidade}/100  —  PRELIMINAR (sujeito à validação final do analista)")
    if res.isento_projeto_por_potencia:
        st.info("🔋 Obra isenta de Projeto Elétrico (TR até 300 kVA). O projeto não é exigido nesta análise.")

    for aviso in ext.get("avisos_datas", []):
        st.warning(aviso)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Documentos reconhecidos")
        for d in res.documentos_reconhecidos:
            st.write("✓", d)
        st.subheader("Documentos faltantes")
        for d in res.documentos_faltantes:
            st.write("✗", d)
    with c2:
        st.subheader("Pendências e alertas")
        for p in list(res.pendencias_condicionais) + list(res.alertas_ativos):
            st.write("⚠️", p)
        st.subheader("Recomendações")
        for r in res.recomendacoes:
            st.write("→", r)

    st.info(f"**Próximo passo:** {res.proximo_passo}")
    st.markdown(f"**Justificativa técnica:** {res.justificativa_tecnica}")

    st.download_button(
        "Baixar resultado (JSON)",
        json.dumps(res.para_dict(), ensure_ascii=False, indent=2, default=str),
        file_name=f"resultado_{res.numero_protocolo}.json",
        mime="application/json",
    )