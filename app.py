import streamlit as st
import pdfplumber
import re
import json
from openai import OpenAI

# ==========================================
# 1. Configuração de Interface e Estética
# ==========================================
st.set_page_config(page_title="AutoStager AI (b-Med)", page_icon="🩺", layout="wide")

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
    """Usa a OpenAI para entender o contexto complexo do laudo."""
    try:
        client = OpenAI(api_key=api_key)
        
        prompt = """
        Você é um patologista sênior. Leia o laudo anatomopatológico e extraia as informações estritamente no formato JSON abaixo.
        
        REGRAS IMPORTANTES:
        1. Esvaziamento Cervical: O laudo pode ter vários níveis. SOME todos os linfonodos acometidos e todos os retirados (Ex: se Nível II 0/5 e Nível I-III 1/18, o total é 1/23).
        2. DOI e Foco: Converta valores para mm. (ex: >20,0 mm = 20.0).
        3. Tamanho do Tumor: Converta para cm.
        4. Margens: Resuma todas as informações de margens em uma frase curta (Ex: "Margem profunda coincidente, demais livres").
        5. ENE (Extravasamento Capsular): true ou false. E no ene_texto coloque "detectado" ou "não detectado".
        
        Formato de saída esperado (APENAS JSON):
        {
            "tipo_histologico": "",
            "tamanho_tumor_cm": 0.0,
            "doi_mm": 0.0,
            "invasao_angio": "",
            "invasao_peri": "",
            "wpoi": "",
            "margens_resumo": "",
            "linfonodos_acometidos": 0,
            "linfonodos_retirados": 0,
            "maior_foco_mm": 0.0,
            "ene": false,
            "ene_texto": ""
        }
        
        Laudo:
        """ + texto

        response = client.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": "Você é um assistente médico especializado em extração de dados JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" }
        )
        
        dados_json = json.loads(response.choices[0].message.content)
        dados_json["resumo_medico"] = gerar_resumo_medico(dados_json)
        return dados_json
        
    except Exception as e:
        st.error(f"Erro na API da OpenAI: {e}")
        return None

def extrair_com_regex(texto):
    """Motor de contingência gratuito caso a API falhe."""
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

    margens_matches = re.findall(r'([^.\n]*margem[^.\n]*(?:coincidente|livre|comprometida|ex[íi]gua)[^.\n]*)', texto, re.IGNORECASE)
    if margens_matches:
        dados["margens_resumo"] = "; ".join(list(set([m.strip() for m in margens_matches])))
    
    dados["resumo_medico"] = gerar_resumo_medico(dados)
    return dados

# ==========================================
# 3. Lógica de Estadiamento (TNM 9) e Conduta NCCN
# ==========================================

def calcular_ptnm(t_tumor, doi, n_pos, ene):
    if t_tumor <= 2 and doi <= 5: pT = 1
    elif (t_tumor <= 2 and doi <= 10) or (t_tumor <= 4 and doi <= 10): pT = 2
    elif t_tumor > 4 or doi > 10: pT = 3
    else: pT = 4
    
    if n_pos == 0: pN = 0
    elif ene: pN = 3
    else: pN = 1 if n_pos == 1 else 2
        
    return pT, pN

def conduta_nccn_cavidade_oral(pT, pN, ene, margens_comprometidas, outras_caracteristicas_adversas):
    """Motor de Decisão NCCN (Páginas OR-2, OR-3 e FOLL-A)"""
    adjuvancia = ""
    tem_risco_adverso = ene or margens_comprometidas or outras_caracteristicas_adversas or (pT in [3, 4]) or (pN in [2, 3])
    
    if pT in [1, 2] and pN == 0:
        if not tem_risco_adverso:
            adjuvancia = "Acompanhamento (Observação) sem terapia adjuvante."
        else:
            if ene: adjuvancia = "Terapia Sistêmica + Radioterapia (Categoria 1)."
            elif margens_comprometidas: adjuvancia = "Re-ressecção (se viável). Considerar RT se margens ficarem negativas OU Terapia Sistêmica + RT."
            else: adjuvancia = "Radioterapia (RT) OU Considerar Terapia Sistêmica + RT."
    else:
        if pN == 1 and not tem_risco_adverso: adjuvancia = "Considerar Radioterapia (RT)."
        elif pT == 3 and pN == 0 and not (ene or margens_comprometidas or outras_caracteristicas_adversas): adjuvancia = "Considerar Radioterapia (RT)."
        else:
            if ene: adjuvancia = "Terapia Sistêmica + Radioterapia (Categoria 1)."
            elif margens_comprometidas: adjuvancia = "Re-ressecção (se viável) OU Terapia Sistêmica + RT (Categoria 1) OU RT."
            else: adjuvancia = "Radioterapia (RT) OU Considerar Terapia Sistêmica + RT."

    vai_irradiar = "Radioterapia" in adjuvancia or "RT" in adjuvancia
    
    exames = [
        "- Avaliação odontológica, de fala, audição e deglutição.",
        "- Avaliação nutricional e aconselhamento para cessação de tabagismo/álcool."
    ]
    if vai_irradiar:
        exames.append("- TSH a cada 6-12 meses (devido à irradiação cervical).")
        exames.append("- Considerar ultrassom venoso com doppler do pescoço a cada 3 anos.")
        
    follow_up = [
        "- Ano 1: Exame físico a cada 1 a 3 meses.",
        "- Ano 2: Consultas a cada 2 a 6 meses.",
        "- Anos 3 a 5: Consultas a cada 4 a 8 meses.",
        "- Após 5 anos: Consultas anuais."
    ]

    return adjuvancia, exames, follow_up

# ==========================================
# 4. Funções Auxiliares
# ==========================================

def ler_pdf(arquivo):
    try:
        with pdfplumber.open(arquivo) as pdf:
            return "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
    except: return ""

def resetar_analise():
    st.session_state.analisado = False
    st.session_state.dados = {}

# ==========================================
# 5. Interface Principal
# ==========================================

def main():
    st.title("AutoStager AI (b-Med) 🩺")
    st.caption("Leitura Avançada de Laudos Oncológicos e Decisão Clínica (NCCN) | Beyond Health BR")

    if 'analisado' not in st.session_state:
        st.session_state.analisado = False
    if 'dados' not in st.session_state:
        st.session_state.dados = {}

    # --- SIDEBAR: ESCOLHA DE MODELO ---
    with st.sidebar:
        st.header("⚙️ Configuração da IA")
        
        # Dicionário: O que o utilizador vê -> O que a API recebe
        mapa_modelos = {
            "gpt-4o-mini (Nossa Escolha)": "gpt-4o-mini",
            "gpt-4o (Premium)": "gpt-4o",
            "gpt-5-nano (Mais Barato)": "gpt-5-nano",
            "gpt-5-mini": "gpt-5-mini",
            "gpt-5.4-nano": "gpt-5.4-nano",
            "Regex (Gratuito/Offline)": "Regex"
        }
        
        # O selectbox mostra apenas as chaves (os nomes amigáveis)
        nome_exibicao = st.selectbox(
            "Selecione o Motor de Inferência:",
            list(mapa_modelos.keys()),
            index=0, # O índice 0 agora é o gpt-4o-mini
            help="O gpt-4o-mini é o nosso modelo de eleição: excelente raciocínio clínico por uma fração do custo."
        )
        
        # A variável que vai para o resto do código recebe o valor real (ex: "gpt-4o-mini")
        modelo_selecionado = mapa_modelos[nome_exibicao]
        
        # Pega a chave da API dos secrets
        api_key = st.secrets.get("OPENAI_API_KEY", "")
        
        if "gpt" in modelo_selecionado and not api_key:
            st.error("⚠️ OpenAI API Key não configurada. Adicione aos Secrets ou use a opção Regex.")
        
        st.divider()
        if st.session_state.analisado:
            if st.button("⬅️ Nova Análise / Voltar", use_container_width=True):
                resetar_analise()
                st.rerun()

    # --- TELA 1: ENTRADA ---
    if not st.session_state.analisado:
        st.subheader("1. Importar Laudo")
        tab_texto, tab_pdf, tab_manual = st.tabs(["📝 Colar Texto (Rápido)", "📄 Upload PDF", "✍️ Manual"])
        
        with tab_texto:
            txt = st.text_area("Cole o texto do laudo aqui:", height=250)
            if st.button("🔍 Processar Laudo", type="primary", use_container_width=True):
                if txt.strip():
                    texto_seguro = anonimizar_texto(txt)
                    with st.spinner(f"Lendo laudo com {modelo_selecionado}..."):
                        if "gpt" in modelo_selecionado and api_key:
                            st.session_state.dados = extrair_com_gpt(texto_seguro, modelo_selecionado, api_key) or {}
                        else:
                            st.session_state.dados = extrair_com_regex(texto_seguro)
                        st.session_state.analisado = True
                        st.rerun()

        with tab_pdf:
            up = st.file_uploader("Upload do arquivo PDF", type="pdf")
            if st.button("🔍 Extrair do PDF", type="primary", use_container_width=True):
                if up:
                    texto_seguro = anonimizar_texto(ler_pdf(up))
                    with st.spinner(f"Lendo laudo com {modelo_selecionado}..."):
                        if "gpt" in modelo_selecionado and api_key:
                            st.session_state.dados = extrair_com_gpt(texto_seguro, modelo_selecionado, api_key) or {}
                        else:
                            st.session_state.dados = extrair_com_regex(texto_seguro)
                        st.session_state.analisado = True
                        st.rerun()

        with tab_manual:
            st.info("Pule a IA e preencha as variáveis diretamente.")
            if st.button("🔓 Abrir Formulário Vazio", use_container_width=True):
                st.session_state.dados = {}
                st.session_state.analisado = True
                st.rerun()

    # --- TELA 2: RESULTADOS E VALIDAÇÃO ---
    else:
        d = st.session_state.dados

        # Resumo Clínico para Copiar
        st.subheader("📋 Resumo do Laudo (Pronto para Cópia)")
        texto_resumo = d.get("resumo_medico", "Nenhum dado extraído.")
        st.info(texto_resumo)

        st.divider()
        st.subheader("2. Estadiamento Patológico e Decisão Clínica")

        with st.form("valida_form"):
            c1, c2, c3 = st.columns(3)
            
            with c1:
                st.markdown("### **Tumor Primário (T)**")
                t_tumor = st.number_input("Tamanho do Tumor (cm)", value=float(d.get("tamanho_tumor_cm", 0.0)))
                doi = st.number_input("DOI (mm)", value=float(d.get("doi_mm", 0.0)))

            with c2:
                st.markdown("### **Linfonodos (N)**")
                n_pos = st.number_input("Acometidos (+)", value=int(d.get("linfonodos_acometidos", 0)))
                n_total = st.number_input("Total Analisados", value=int(d.get("linfonodos_retirados", 0)))
                ene = st.checkbox("Extravasamento Capsular (ENE)", value=bool(d.get("ene", False)))

            with c3:
                st.markdown("### **Características Adicionais**")
                wpoi = st.text_input("WPOI", value=str(d.get("wpoi", "")))
                
                # ==============================================================
                # NOVA TRAVA DE SEGURANÇA AGRESSIVA PARA MARGENS
                # ==============================================================
                texto_margem = str(d.get("status_margens", "")) + " " + str(d.get("margens_resumo", ""))
                texto_margem = texto_margem.lower()
                
                if re.search(r'(coincidente|comprometida|positiva|infiltrada)', texto_margem):
                    status_final = "Comprometidas"
                elif re.search(r'(ex[íi]gua|<1|< 1)', texto_margem):
                    status_final = "Exíguas (<1mm)"
                else:
                    status_final = "Livres"
                
                opcoes_margem = ["Livres", "Comprometidas", "Exíguas (<1mm)"]
                idx_margem = opcoes_margem.index(status_final)
                
                margem = st.selectbox("Status Geral das Margens", opcoes_margem, index=idx_margem)

            submit = st.form_submit_button("📊 Confirmar e Calcular pTNM Final", type="primary", use_container_width=True)

        if submit:
            pT_num, pN_num = calcular_ptnm(t_tumor, doi, n_pos, ene)
            pT_str = f"pT{pT_num}"
            pN_str = f"pN{pN_num}" + ("b" if ene else "")
            
            margem_comp = margem != "Livres"
            outros_riscos = wpoi == "5" or (d.get("invasao_peri") not in ["Não detectada", "Não identificado", ""]) or (d.get("invasao_angio") not in ["Não detectada", "Não identificado", ""])
            
            adjuvancia, exames, cronograma = conduta_nccn_cavidade_oral(
                pT_num, pN_num, ene, margem_comp, outros_riscos
            )
            
            col_res1, col_res2 = st.columns([1, 2])
            col_res1.metric(label="Estadiamento (TNM 9)", value=f"{pT_str} {pN_str}")
            
            with col_res2:
                if ene: st.error("🚨 Alto Risco: Extravasamento Capsular Confirmado. Indicação de QtRt.")
                if wpoi == "5": st.warning("🚨 Alerta: WPOI (5) sinaliza pior padrão de invasão.")
                if margem_comp: st.warning(f"🚨 Alerta: Margens {margem}.")

            st.divider()
            st.subheader("💡 Diretrizes de Conduta (NCCN v1.2026)")
            
            aba_adj, aba_exames, aba_foll = st.tabs(["💊 Adjuvância", "🩸 Exames e Reabilitação", "📅 Follow-up"])
            
            with aba_adj: st.info(f"**Indicação:** {adjuvancia}")
            with aba_exames: st.write("\n".join(exames))
            with aba_foll: st.write("\n".join(cronograma))

        if st.button("🔄 Fechar e Iniciar Novo Caso", type="secondary"):
            resetar_analise()
            st.rerun()

    st.divider()
    st.info("🔒 **Privacidade by Design & Compliance:** Nenhuma informação clínica, laudo em PDF ou dado do paciente fica armazenado em nossos servidores. O processamento ocorre localmente em memória volátil e é completamente destruído após a análise e fechamento da sessão.")
    st.caption("A plataforma Beyond Health BR (b-Med) opera em estrita conformidade com a **LGPD** e segue integralmente as resoluções do **CFM**. Esta ferramenta atua como auxílio ao julgamento clínico e não substitui a validação final do médico assistente.")

if __name__ == "__main__":
    main()