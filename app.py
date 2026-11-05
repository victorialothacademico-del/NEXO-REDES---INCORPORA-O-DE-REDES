import json
import os
import tempfile
import shutil
from datetime import datetime

import streamlit as st

from pipeline import (
    SolicitacaoIncorporacao,
    validar_documentacao,
    analisar_pasta_pdfs,
    MODELO_DECISAO,
    MODELO_EXTRACAO_PDF,
)

st.set_page_config(page_title="NEXO REDES | Gestão de processos", page_icon="⚡", layout="wide")

st.markdown("""
<style>
:root { --navy:#102D40; --blue:#1C526E; --accent:#4386A2; }
.block-container {padding-top: 2rem; max-width: 1180px;}
[data-testid="stSidebar"] {background: #102D40;}
[data-testid="stSidebar"] * {color: #F4F7F9 !important;}
.nexo-brand {font-size: 2rem; font-weight: 800; letter-spacing: .04em; color:#102D40;}
.nexo-sub {color:#607987; font-size:.78rem; letter-spacing:.12em;}
.nexo-card {background:#F4F7F9; border:1px solid #E0E8ED; border-radius:14px; padding:18px;}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("# ⚡ NEXO REDES")
    st.caption("GESTÃO INTELIGENTE DE REDES")
    st.divider()
    st.markdown("**Portal de solicitações**")
    st.caption("Protótipo de desenvolvimento — acesso local, sem autenticação ainda.")
    st.divider()
    st.markdown("**Etapas**")
    st.markdown("1. Identificação da obra\n2. Dados técnicos\n3. Documentação\n4. Análise inteligente")

st.markdown('<div class="nexo-brand">NEXO REDES</div><div class="nexo-sub">PORTAL DE GESTÃO E ANÁLISE DE REDES DE DISTRIBUIÇÃO</div>', unsafe_allow_html=True)
st.title("Nova solicitação")
st.write("Cadastre as informações da obra e envie os documentos para uma análise documental assistida por inteligência artificial.")
st.info("Esta versão é um protótipo para testes. Não envie documentos pessoais, bancários ou dados reais de clientes até concluirmos autenticação, controle de acesso e armazenamento seguro.")

TIPOS = {
    "Incorporação de redes": "incorporacao_rede",
    "Doação de rede ou transformador novo": "doacao_rede_nova",
    "Doação de rede ou transformador existente": "doacao_rede_existente",
}

with st.form("form_obra", clear_on_submit=False):
    st.subheader("1. Identificação da solicitação")
    c1, c2 = st.columns(2)
    with c1:
        tipo_label = st.selectbox("Modalidade *", list(TIPOS))
        nome_obra = st.text_input("Nome da obra / empreendimento *")
        empresa = st.text_input("Empresa / responsável pela obra *")
        nome_responsavel = st.text_input("Nome do responsável técnico")
        cpf_cnpj = st.text_input("CPF/CNPJ do responsável")
    with c2:
        protocolo = st.text_input("Protocolo (se já existir)", value=f"PRE-{datetime.now():%Y%m%d-%H%M%S}")
        endereco = st.text_input("Endereço da obra *")
        municipio = st.text_input("Município / UF *")
        email = st.text_input("E-mail para contato *")
        telefone = st.text_input("Telefone")

    st.divider()
    st.subheader("2. Características técnicas")
    c1, c2, c3 = st.columns(3)
    with c1:
        quantidade_tr = st.number_input("Quantidade de transformadores", min_value=0, step=1, value=0)
    with c2:
        potencia_total = st.number_input("Potência total dos transformadores (kVA)", min_value=0.0, step=15.0, value=0.0)
    with c3:
        tr_acima_300_label = st.selectbox("Há transformador acima de 300 kVA?", ["Não sei / confirmar", "Não — até 300 kVA", "Sim — acima de 300 kVA"])
    descricao = st.text_area("Descrição da obra e informações complementares", height=100)
    loteamento = st.radio("A obra corresponde exclusivamente a loteamento de lotes de terreno?", ["Não", "Sim"], horizontal=True)
    st.caption("Se selecionar Sim, a declaração de iluminação pública será incluída como exigência documental.")

    st.divider()
    st.subheader("3. Condições específicas")
    tipo = TIPOS[tipo_label]
    restituicao = st.checkbox("Há restituição de valores ao cliente?", disabled=(tipo != "incorporacao_rede"))
    condicoes_isencao = st.checkbox("Confirmo que não é acesso à geração distribuída, migração para mercado livre ou situação omissa à norma técnica.")

    st.divider()
    st.subheader("4. Documentos da obra")
    st.markdown("**Atenção: envie toda a documentação reunida em um único arquivo PDF.**")
    st.markdown("Organize o PDF obrigatoriamente nesta ordem inicial:")
    st.markdown("1. **Orçamento de Conexão**")
    st.markdown("2. **Projeto Elétrico**")
    st.markdown("3. **Demais documentos exigidos**, conforme a modalidade do processo (ART/TRT, notas fiscais, documentos pessoais, laudos, autorizações e outros aplicáveis).")
    st.info("Antes de enviar, confira se todos os documentos estão no mesmo PDF e se o Orçamento de Conexão e o Projeto Elétrico aparecem nas duas primeiras posições.")
    arquivo_pdf = st.file_uploader("Anexe o arquivo único com toda a documentação (somente PDF)", type=["pdf"], accept_multiple_files=False)
    enviar = st.form_submit_button("Iniciar análise documental", type="primary", use_container_width=True)

if enviar:
    obrigatorios = [nome_obra, empresa, endereco, municipio, email]
    if any(not str(x).strip() for x in obrigatorios):
        st.error("Preencha os campos obrigatórios de identificação da obra.")
    elif not arquivo_pdf:
        st.error("Anexe um único arquivo PDF contendo toda a documentação da obra.")
    elif not os.getenv("GEMINI_API_KEY") and "GEMINI_API_KEY" not in st.secrets:
        st.error("Configure GEMINI_API_KEY antes de executar a análise.")
    else:
        if "GEMINI_API_KEY" in st.secrets:
            os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
        pasta = tempfile.mkdtemp(prefix="nexo_")
        try:
            safe_name = os.path.basename(arquivo_pdf.name).replace("..", "_")
            with open(os.path.join(pasta, safe_name), "wb") as dest:
                dest.write(arquivo_pdf.getbuffer())
            with st.spinner("Lendo os documentos e extraindo informações com Gemini..."):
                ext = analisar_pasta_pdfs(pasta, tipo, modelo=MODELO_EXTRACAO_PDF)
            info = ext.get("info_geral", {})
            tr_acima_300 = {"Não sei / confirmar": None, "Não — até 300 kVA": False, "Sim — acima de 300 kVA": True}[tr_acima_300_label]
            pot = potencia_total if potencia_total > 0 else ext.get("potencia_transformador_kva")
            observacoes = (
                f"Nome da obra: {nome_obra}. Empresa: {empresa}. Município/UF: {municipio}. "
                f"E-mail: {email}. Telefone: {telefone}. Quantidade de transformadores: {quantidade_tr}. "
                f"Potência total informada (kVA): {potencia_total}. Descrição: {descricao}"
            )
            sol = SolicitacaoIncorporacao(
                numero_protocolo=protocolo,
                tipo_solicitacao=tipo,
                nome_responsavel=nome_responsavel or info.get("nome_responsavel") or empresa,
                cpf_cnpj=cpf_cnpj or info.get("cpf_cnpj") or "Não informado",
                endereco_obra=f"{endereco}, {municipio}",
                descricao_obra=descricao or info.get("descricao_obra") or nome_obra,
                documentos_apresentados=ext.get("documentos_apresentados", []),
                data_aprovacao_projeto=ext.get("data_aprovacao_projeto"),
                data_aprovacao_orcamento=ext.get("data_aprovacao_orcamento"),
                possui_restituicao_valores=(restituicao and tipo == "incorporacao_rede"),
                potencia_transformador_kva=pot,
                tr_acima_300kva=tr_acima_300,
                condicoes_isencao_confirmadas=(True if condicoes_isencao else None),
                is_loteamento_lotes_terreno=(loteamento == "Sim"),
                observacoes_adicionais=observacoes,
            )
            with st.spinner("Aplicando checklist e regras de validação..."):
                res = validar_documentacao(sol, modelo=MODELO_DECISAO)
            st.session_state["nexo_resultado"] = res
            st.session_state["nexo_extracao"] = ext
            st.session_state["nexo_dados"] = {"obra":nome_obra,"empresa":empresa,"tipo":tipo_label,"loteamento":loteamento}
        except Exception as e:
            st.exception(e)
        finally:
            shutil.rmtree(pasta, ignore_errors=True)

res = st.session_state.get("nexo_resultado")
if res:
    ext = st.session_state.get("nexo_extracao", {})
    dados = st.session_state.get("nexo_dados", {})
    st.divider()
    st.header("Resultado da análise")
    st.caption(f"{dados.get('obra','')} · {dados.get('tipo','')} · Protocolo {res.numero_protocolo}")
    if res.status == "APROVADO": st.success(f"APROVADO · Conformidade {res.score_conformidade}/100")
    elif res.status == "REPROVADO": st.error(f"REPROVADO · Conformidade {res.score_conformidade}/100")
    else: st.warning(f"APROVADO COM RESSALVAS · Conformidade {res.score_conformidade}/100")
    if getattr(res, "isento_projeto_por_potencia", False):
        st.info("A regra de isenção de projeto por potência foi aplicada; confirme as condições normativas com o analista.")
    c1,c2 = st.columns(2)
    with c1:
        st.subheader("Documentos reconhecidos")
        for d in res.documentos_reconhecidos: st.write("✓",d)
        st.subheader("Documentos faltantes")
        for d in res.documentos_faltantes: st.write("•",d)
    with c2:
        st.subheader("Pendências e alertas")
        for d in list(res.pendencias_condicionais)+list(res.alertas_ativos): st.write("⚠️",d)
        st.subheader("Recomendações")
        for d in res.recomendacoes: st.write("→",d)
    st.info(f"Próximo passo: {res.proximo_passo}")
    st.write("**Justificativa técnica**")
    st.write(res.justificativa_tecnica)
    with st.expander("Auditoria da extração dos PDFs"):
        st.json(ext.get("analises_detalhadas", []))
    st.download_button("Baixar relatório JSON", json.dumps(res.para_dict(),ensure_ascii=False,indent=2,default=str), file_name=f"nexo_{res.numero_protocolo}.json", mime="application/json")
