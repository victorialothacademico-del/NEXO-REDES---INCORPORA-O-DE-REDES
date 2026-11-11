import os, re, json, random, tempfile, shutil, secrets
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

# ---------------- ACOMPANHAMENTO PELO RT ----------------
_ALFABETO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sem 0/O/1/I para evitar confusão

def gerar_codigo():
    return "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(8))

def fmt_codigo(c):
    c = str(c or "")
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c

def norm_codigo(c):
    return re.sub(r"[^A-Za-z0-9]", "", str(c or "")).upper()

ROTULO_STATUS = {
    "APROVADO": "APROVADO",
    "APROVADO_COM_RESSALVAS": "APROVADO COM RESSALVAS",
    "REPROVADO": "REPROVADO",
}

def mostrar_consulta(sub):
    """Mostra ao RT o andamento de UMA obra. Só exibe o resultado como DEFINITIVO depois
    que o analista validou; antes disso, informa que ainda está em análise."""
    r = sub.get("resultado") or {}
    st.markdown(f"### Protocolo {sub.get('protocolo')}")
    enviado = str(sub.get("criado_em") or "")[:16].replace("T", " ")
    st.caption(f"Enviado em {enviado}")

    if not sub.get("validado_em"):
        st.info("⏳ **Em análise.** Sua documentação foi recebida e processada, "
                "mas ainda aguarda a validação final do analista. "
                "Volte a consultar mais tarde com o mesmo código.")
        with st.expander("Ver análise preliminar (automática, sujeita a alteração)"):
            st.write(f"Situação preliminar: **{ROTULO_STATUS.get(sub.get('status'), sub.get('status'))}** "
                     f"— conformidade {sub.get('score')}/100")
            for x in r.get("documentos_faltantes", []):
                st.write("✗", x)
            for x in list(r.get("pendencias_condicionais", [])) + list(r.get("alertas_ativos", [])):
                st.write("⚠️", x)
        return

    final = sub.get("decisao_final")
    texto = ROTULO_STATUS.get(final, final)
    {"APROVADO": st.success, "REPROVADO": st.error}.get(final, st.warning)(
        f"✅ Análise concluída — **{texto}**  "
        f"(validado em {str(sub['validado_em'])[:10]})"
    )
    if sub.get("observacao"):
        st.markdown("**Observação do analista**")
        st.write(sub["observacao"])

    # Pontos de atenção só aparecem se o resultado final não for aprovação plena
    if final != "APROVADO":
        faltantes = r.get("documentos_faltantes", [])
        pend = list(r.get("pendencias_condicionais", [])) + list(r.get("alertas_ativos", []))
        recs = r.get("recomendacoes", [])
        if faltantes or pend or recs:
            st.markdown("**Pontos de atenção da análise**")
            for x in faltantes:
                st.write("✗", x)
            for x in pend:
                st.write("⚠️", x)
            for x in recs:
                st.write("→", x)
    if r.get("proximo_passo") and final != "APROVADO":
        st.info(f"**Próximo passo sugerido:** {r['proximo_passo']}")


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
    cols = ["criado_em", "protocolo", "codigo_consulta", "responsavel_email", "tipo", "status",
            "decisao_final", "analista", "divergiu_da_ia", "validado_em"]
    st.dataframe(pd.DataFrame(dados).reindex(columns=cols), use_container_width=True)

    st.subheader("Responsáveis cadastrados")
    rts = db().table("responsaveis").select("*").order("criado_em", desc=True).execute().data
    st.dataframe(pd.DataFrame(rts), use_container_width=True)
    st.stop()

# ---------------- NAVEGAÇÃO ----------------
st.title("🔌 Validação Documental — Incorporação/Doação de Rede")
st.caption("CEEE-D / Equatorial Energia | NT.017.EQTL – Revisão 04")

pagina = st.radio("Menu", ["Enviar nova obra", "Consultar andamento"],
                  horizontal=True, label_visibility="collapsed")

# ---------------- CONSULTA DE ANDAMENTO (RT) ----------------
if pagina == "Consultar andamento":
    st.subheader("Consultar andamento da obra")
    st.write("Informe o e-mail do cadastro e o **código de acompanhamento** que apareceu "
             "quando você enviou a documentação.")
    with st.form("consulta"):
        c_email = st.text_input("E-mail do cadastro")
        c_codigo = st.text_input("Código de acompanhamento (ex.: ABCD-EFGH)")
        buscar = st.form_submit_button("Consultar", type="primary")
    if buscar:
        codigo = norm_codigo(c_codigo)
        if "@" not in c_email or len(codigo) != 8:
            st.error("Informe um e-mail válido e o código de 8 caracteres.")
        else:
            try:
                achou = (db().table("submissoes").select("*")
                         .eq("responsavel_email", c_email.strip().lower())
                         .eq("codigo_consulta", codigo)
                         .order("criado_em", desc=True).limit(1).execute().data)
            except Exception as e:
                achou = None
                st.error(f"Não foi possível consultar agora: {e}")
            if achou is not None:
                if not achou:
                    st.error("Nenhuma obra encontrada com esse e-mail e código. Confira os dados.")
                else:
                    mostrar_consulta(achou[0])
    st.stop()

# ---------------- TELA 1: CADASTRO ----------------

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

        registro = {
            "responsavel_email": rt["email"], "protocolo": protocolo, "tipo": tipo,
            "status": res.status, "score": res.score_conformidade,
            "resultado": json.loads(json.dumps(res.para_dict(), default=str)),
            "arquivos": caminhos,
        }
        codigo = gerar_codigo()
        try:
            db().table("submissoes").insert({**registro, "codigo_consulta": codigo}).execute()
        except Exception as e:
            if "codigo_consulta" not in str(e):
                raise
            # coluna ainda não criada no Supabase: salva sem código para não perder a obra
            db().table("submissoes").insert(registro).execute()
            codigo = None

        st.session_state["resultado"] = res
        st.session_state["extracao"] = ext
        st.session_state["codigo_consulta"] = codigo
    except Exception as e:
        st.error(f"Erro no processamento: {e}")
    finally:
        shutil.rmtree(pasta, ignore_errors=True)

# ---------------- TELA 3: RESULTADO ----------------
res = st.session_state.get("resultado")
if res:
    ext = st.session_state["extracao"]
    cod = st.session_state.get("codigo_consulta")
    if cod:
        st.success(f"📌 **Código de acompanhamento: {fmt_codigo(cod)}** — guarde este código. "
                   f"Use-o com o seu e-mail na opção *Consultar andamento* para ver "
                   f"a decisão final do analista.")
    mostrar = {"APROVADO": st.success, "REPROVADO": st.error,
               "APROVADO_COM_RESSALVAS": st.warning}.get(res.status, st.info)
    mostrar(f"{res.status}  |  Conformidade: {res.score_conformidade}/100  —  PRELIMINAR (sujeito à validação final do analista)")
    if res.isento_projeto_por_potencia:
        st.info("🔋 Obra isenta de Projeto Elétrico (TR até 300 kVA). O projeto não é exigido nesta análise.")

    for aviso in ext.get("avisos_datas", []):
        st.warning(aviso)

    # Arquivos que deram erro na leitura (ex.: cota do Gemini) — antes só apareciam no log
    for an in ext.get("analises_detalhadas", []):
        for item in an.get("resultado", []):
            doc = str(item.get("documento_identificado", ""))
            if doc.startswith("ERRO NA ANÁLISE"):
                st.error(f"⚠️ O arquivo '{an['arquivo']}' não pôde ser lido: {doc[len('ERRO NA ANÁLISE: '):][:300]}. "
                         f"Os documentos dele podem aparecer como ausentes — reprocesse ou confira o arquivo.")

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