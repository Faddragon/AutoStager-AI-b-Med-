import streamlit as st
import pdfplumber
import re
import json
from openai import OpenAI

# ==========================================
# 1. Configuração de Interface e Segurança
# ==========================================
st.set_page_config(page_title="AutoStager AI (b-Med)", page_icon="🩺", layout="wide")

# FUNÇÃO DE SEGURANÇA (LGPD/CFM)
def check_password():
    """Retorna True se o usuário inseriu a senha correta configurada nos Secrets."""
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Remove a senha da memória por segurança
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.title("AutoStager AI (b-Med) 🩺")
        st.text_input("Insira a senha de acesso para começar:", type="password", on_change=password_entered, key="password")
        st.info("Acesso restrito para testes clínicos e validação interna. Beyond Health BR.")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Senha incorreta. Tente novamente:", type="password", on_change=password_entered, key="password")
        st.error("🔒 Acesso negado.")
        return False
    else:
        return True

# BLOQUEIO DE ACESSO
if not check_password():
    st.stop()

def anonimizar_texto(texto):
    return re.sub(r'\d{3}\.\d{3}\.\d{3}-\d{2}', '[CPF REMOVIDO]', texto)

# ==========================================
# 2. Motores de Extração (IA e Regex)
# ==========================================

def gerar_resumo_medico(dados):
    """Gera o texto formatado para copiar e colar no prontuário."""
    return (
        f"**Tipo histológico:** {dados.get('tipo_histologico', 'Não identificado')}\n\n"
        f"**Tamanho do tumor:** {dados.get('tamanho_tumor_cm', 0.0)} cm\n\n"
        f"**DOI:** {dados.get('doi_mm', 0.0)} mm\n\n"
        f"**Invasão angiolinfática:** {dados.get('invasao_angio', 'Não detectada')}\n\n"
        f"**Invasão perineural:** {dados.get('invasao_peri', 'Não detectada')}\n\n"
        f"**WPOI:** {dados.get('wpoi', 'N/A')}\n\n"
        f"**Margens:** {dados.get('margens_resumo', 'Não descritas')}\n\n"
        f"**Esvaziamento cervical:** {dados.get('linfonodos_acometidos', 0)}/{dados.get('linfonodos_retirados', 0)}, "
        f"com maior foco de {dados.get('maior_foco_mm', 0.0)} mm e extravasamento capsular {dados.get('ene_texto', 'não detectado')}."
    )

def extrair_com_gpt(texto, modelo, api_key):
    try:
        client = OpenAI(api_key=api_key)
        prompt = """
        Você é um patologista sênior. Leia o laudo e extraia as informações em JSON.
        REGRAS:
        1. Esvaziamento Cervical: SOME todos os linfonodos acometidos e retirados de todos os níveis.
        2. DOI e Foco: Converta para mm (ex: >20mm = 20.0).
        3. Tamanho do Tumor: Converta para cm.
        4. Margens: Resuma em uma frase.
        5. status_margens: Se contiver "coincidente", "comprometida" ou "positiva", retorne "Comprometidas". Senão "Livres".
        6. ENE: true/false.
        
        {
            "tipo_histologico": "", "tamanho_tumor_cm": 0.0, "doi_mm": 0.0,
            "invasao_angio": "", "invasao_peri": "", "wpoi": "",
            "margens_resumo": "", "status_margens": "Livres",
            "linfonodos_acometidos": 0, "linfonodos_retirados": 0,
            "maior_foco_mm": 0.0, "ene": false, "ene_texto": ""
        }
        Laudo:
        """ + texto

        response = client.chat.completions.create(
            model=modelo,
            messages=[{"role": "system", "content": "Assistente médico JSON."}, {"role": "user", "content": prompt}],
            response_format={ "type": "json_object" }
        )
        dados_json = json.loads(response.choices[0].message.content)
        dados_json["resumo_medico"] = gerar_resumo_medico(dados_json)
        return dados_json
    except Exception as e:
        st.error(f"Erro na OpenAI: {e}")
        return None

def extrair_com_regex(texto):
    dados = {
        "tipo_histologico": "Não identificado", "tamanho_tumor_cm": 0.0, "doi_mm": 0.0,
        "invasao_angio": "Não detectada", "invasao_peri": "Não detectada", "wpoi": "N/A",
        "margens_resumo": "Verificar laudo original", "status_margens": "Livres",
        "linfonodos_acometidos": 0, "linfonodos_retirados": 0, "maior_foco_mm": 0.0, "ene": False, "ene_texto": "não detectado"
    }
    doi_match = re.search(r'(?:DOI|profundidade)[^\d]+(\d+[,.]?\d*)\s*(mm|cm)', texto, re.IGNORECASE)
    if doi_match: dados["doi_mm"] = float(doi_match.group(1).replace(',', '.')) * (10 if doi_match.group(2).lower() == 'cm' else 1)
    
    tumor_match = re.search(r'Tamanho[\s:]+(?:mais\s+de\s+|>\s*)?(\d+[,.]?\d*)\s*(cm|mm)', texto, re.IGNORECASE)
    if tumor_match: dados["tamanho_tumor_cm"] = float(tumor_match.group(1).replace(',', '.')) / (10 if tumor_match.group(2).lower() == 'mm' else 1)
    
    linf_matches = re.findall(r'(\d+)\s*/\s*(\d+)', texto)
    if linf_matches:
        dados["linfonodos_acometidos"] = sum(int(m[0]) for m in linf_matches)
        dados["linfonodos_retirados"] = sum(int(m[1]) for m in linf_matches)

    dados["resumo_medico"] = gerar_resumo_medico(dados)
    return dados

# ==========================================
# 3. Lógica de Estadiamento e NCCN
# ==========================================

def calcular_ptnm(t_tumor, doi, n_pos, ene):
    if t_tumor <= 2 and doi <= 5: pT = 1
    elif (t_tumor <= 2 and doi <= 10) or (t_tumor <= 4 and doi <= 10): pT = 2
    elif t_tumor > 4 or doi > 10: pT = 3
    else: pT = 4
    pN = 0 if n_pos == 0 else (3 if ene else (1 if n_pos == 1 else 2))
    return pT, pN

def conduta_nccn_cavidade_oral(pT, pN, ene, margens_comp, outros_riscos):
    adjuvancia = ""
    tem_risco = ene or margens_comp or outros_riscos or (pT >= 3) or (pN >= 2)
    if pT <= 2 and pN == 0:
        if not tem_risco: adjuvancia = "Observação."
        else: adjuvancia = "Terapia Sistêmica + RT (Categoria 1)" if ene else "Radioterapia (RT)."
    else:
        adjuvancia = "Terapia Sistêmica + RT (Categoria 1)" if ene else "RT ou Sistêmica + RT."
    
    exames = ["- Odonto, Fono e Deglutição.", "- Nutrição e cessação de tabaco."]
    follow = ["- Ano 1: 1-3 meses.", "- Ano 2: 2-6 meses.", "- Anos 3-5: 4-8 meses."]
    return adjuvancia, exames, follow

# ==========================================
# 4. Funções Auxiliares e Main
# ==========================================

def ler_pdf(arquivo):
    try:
        with pdfplumber.open(arquivo) as pdf:
            return "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
    except: return ""

def resetar_analise():
    st.session_state.analisado = False
    st.session_state.dados = {}

def main():
    if 'analisado' not in st.session_state: st.session_state.analisado = False
    if 'dados' not in st.session_state: st.session_state.dados = {}

    with st.sidebar:
        st.header("⚙️ Configuração da IA")
        mapa_modelos = {
            "gpt-4o-mini (Nossa Escolha)": "gpt-4o-mini",
            "gpt-5-nano (Mais Barato)": "gpt-5-nano",
            "gpt-5-mini": "gpt-5-mini",
            "gpt-4.1-nano": "gpt-4.1-nano",
            "gpt-4o (Premium)": "gpt-4o",
            "Regex (Offline)": "Regex"
        }
        nome_exibicao = st.selectbox("Motor de Inferência:", list(mapa_modelos.keys()), index=0)
        modelo_selecionado = mapa_modelos[nome_exibicao]
        api_key = st.secrets.get("OPENAI_API_KEY", "")
        
        if st.session_state.analisado:
            if st.button("⬅️ Nova Análise"): 
                resetar_analise()
                st.rerun()

    if not st.session_state.analisado:
        st.subheader("1. Importar Laudo")
        t1, t2, t3 = st.tabs(["📝 Texto", "📄 PDF", "✍️ Manual"])
        with t1:
            txt = st.text_area("Cole o laudo:", height=200)
            if st.button("🔍 Analisar Texto"):
                if txt:
                    with st.spinner("Processando..."):
                        st.session_state.dados = extrair_com_gpt(anonimizar_texto(txt), modelo_selecionado, api_key) if "gpt" in modelo_selecionado else extrair_com_regex(txt)
                        st.session_state.analisado = True
                        st.rerun()
        with t2:
            up = st.file_uploader("Upload PDF", type="pdf")
            if st.button("🔍 Analisar PDF") and up:
                with st.spinner("Lendo PDF..."):
                    st.session_state.dados = extrair_com_gpt(anonimizar_texto(ler_pdf(up)), modelo_selecionado, api_key) if "gpt" in modelo_selecionado else extrair_com_regex(ler_pdf(up))
                    st.session_state.analisado = True
                    st.rerun()
        with t3:
            if st.button("🔓 Manual"): 
                st.session_state.dados = {}
                st.session_state.analisado = True
                st.rerun()
    else:
        d = st.session_state.dados
        st.info(d.get("resumo_medico", ""))
        with st.form("valida"):
            c1, c2, c3 = st.columns(3)
            with c1:
                t_tumor = st.number_input("Tamanho (cm)", value=float(d.get("tamanho_tumor_cm", 0.0)))
                doi = st.number_input("DOI (mm)", value=float(d.get("doi_mm", 0.0)))
            with c2:
                n_pos = st.number_input("Positivos", value=int(d.get("linfonodos_acometidos", 0)))
                n_total = st.number_input("Total", value=int(d.get("linfonodos_retirados", 1)))
                ene = st.checkbox("ENE", value=bool(d.get("ene", False)))
            with c3:
                wpoi = st.text_input("WPOI", value=str(d.get("wpoi", "")))
                
                # TRAVA DE SEGURANÇA PARA MARGENS
                texto_m = (str(d.get("status_margens", "")) + " " + str(d.get("margens_resumo", ""))).lower()
                status_f = "Comprometidas" if any(x in texto_m for x in ["coincidente", "comprometida", "positiva"]) else ("Exíguas (<1mm)" if "ex" in texto_m else "Livres")
                margem = st.selectbox("Margens", ["Livres", "Comprometidas", "Exíguas (<1mm)"], index=["Livres", "Comprometidas", "Exíguas (<1mm)"].index(status_f))

            if st.form_submit_button("📊 Calcular"):
                pT, pN = calcular_ptnm(t_tumor, doi, n_pos, ene)
                adj, ex, fl = conduta_nccn_cavidade_oral(pT, pN, ene, margem != "Livres", wpoi == "5")
                st.metric("pTNM", f"pT{pT} pN{pN}{'b' if ene else ''}")
                st.write(f"**Conduta:** {adj}")

if __name__ == "__main__":
    main()
