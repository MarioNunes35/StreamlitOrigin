import streamlit as st
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional
import time

# Configuração da página
st.set_page_config(
    page_title="Origin Software Agent",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS personalizado
st.markdown("""
<style>
.main > div {
    padding-top: 2rem;
}
.stChat > div {
    background-color: #f8f9fa;
}
</style>
""", unsafe_allow_html=True)

@st.cache_data
def load_documentation():
    """Carrega documentação com cache do Streamlit"""
    docs_content = """
    ORIGIN SOFTWARE - GUIA ESSENCIAL

    INTRODUÇÃO:
    Origin é um software profissional para análise de dados e criação de gráficos científicos.
    Desenvolvido pela OriginLab Corporation, é amplamente usado em pesquisa e indústria.

    PRINCIPAIS RECURSOS:
    • Importação de dados (Excel, CSV, ASCII, bancos de dados)
    • Gráficos 2D e 3D profissionais
    • Análise estatística avançada
    • Fitting de curvas e superfícies
    • Análise de sinais (FFT, filtros)
    • Programação em LabTalk e Origin C
    • Templates e batch processing
    • Publicação de gráficos de alta qualidade

    INTERFACE PRINCIPAL:
    • Project Explorer: Gerencia arquivos e pastas do projeto
    • Workbook: Planilhas para dados (similar ao Excel)
    • Graph: Janelas de gráficos
    • Matrix: Dados matriciais para gráficos 3D
    • Function: Editor de funções matemáticas

    CRIANDO GRÁFICOS BÁSICOS:
    1. Importar dados: File > Import > Single ASCII
    2. Selecionar colunas de dados
    3. Plot > Basic 2D > Line, Scatter, Column, etc.
    4. Personalizar: duplo clique no gráfico
    5. Plot Details: Controla aparência de linhas, símbolos, cores

    IMPORTAÇÃO DE DADOS:
    • File > Import > Single ASCII para arquivos texto
    • Import Wizard para formatos complexos
    • Suporte nativo para Excel (.xlsx)
    • Data Connector para bancos de dados

    PERSONALIZAÇÃO DE GRÁFICOS:
    • Plot Details (Ctrl+Alt+L): Controla aparência completa
    • Layer: Configurações gerais do gráfico
    • Line: Estilo, cor e espessura das linhas
    • Symbol: Marcadores de pontos
    • Axis: Configuração dos eixos

    ATALHOS ÚTEIS:
    • Ctrl+Alt+L: Abre Plot Details
    • Ctrl+D: Duplica gráfico atual
    • Alt+2: Acesso rápido ao Basic 2D
    • F11: Maximiza janela ativa
    • Ctrl+J: Abre Code Builder (LabTalk)

    ANÁLISE ESTATÍSTICA:
    • Statistics > Descriptive Statistics
    • Statistics > Hypothesis Testing
    • Analysis > Fitting > Linear/Nonlinear Fit
    • Analysis > Signal Processing > FFT

    PROGRAMAÇÃO:
    • LabTalk: Linguagem de script nativa
    • Origin C: Programação em C/C++
    • Python: Suporte via PyOrigin
    • Code Builder: IDE integrado
    """
    return docs_content

def create_origin_agent():
    """Cria agente Origin usando Agno"""
    try:
        from agno.agent import Agent
        from agno.models.anthropic import Claude
        
        # Carrega documentação
        docs = load_documentation()
        
        # Instruções detalhadas
        instructions = f"""
        Você é um ESPECIALISTA AVANÇADO em Origin Software da OriginLab Corporation.

        BASE DE CONHECIMENTO:
        {docs}

        DIRETRIZES DE RESPOSTA:
        1. Use SEMPRE o conhecimento fornecido quando relevante
        2. Seja específico e prático - forneça procedimentos claros
        3. Responda em português quando pergunta for em português
        4. Use markdown para formatação clara
        5. Inclua atalhos de teclado quando relevante
        6. Seja direto ao ponto, sem menções de documentação
        7. Proponha workflows otimizados
        8. Sempre ofereça dicas extras práticas

        ESTILO:
        - Comece direto com a explicação
        - Use listas numeradas para passos
        - Inclua dicas importantes
        - Termine com sugestões úteis
        """
        
        # Cria agente
        agent = Agent(
            name="Origin Software Expert",
            model=Claude(id="claude-3-5-sonnet-20240620"),
            description="Especialista em Origin Software com conhecimento técnico completo",
            instructions=[instructions],
            tools=[],
            markdown=True,
            debug_mode=False,
        )
        
        return agent
        
    except Exception as e:
        st.error(f"Erro ao criar agente: {e}")
        return None

def main():
    """Interface principal do Streamlit"""
    
    # Título
    st.title("🧪 Origin Software Agent")
    st.markdown("**Assistente especializado em Origin Software da OriginLab**")
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Configuração")
        
        # Configuração da API Key
        api_key = st.text_input(
            "Anthropic API Key:",
            type="password",
            help="Insira sua chave da API do Claude"
        )
        
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
            st.success("✅ API Key configurada")
        
        st.divider()
        
        # Informações
        st.header("📚 Sobre")
        st.markdown("""
        Este assistente pode ajudar com:
        - 📊 Criação de gráficos
        - 📈 Importação de dados  
        - 📋 Análise estatística
        - 🔧 LabTalk e Origin C
        - ⚡ Atalhos e workflows
        """)
        
        st.divider()
        
        # Exemplos de perguntas
        st.header("💡 Exemplos")
        example_questions = [
            "Como criar um gráfico de dispersão?",
            "Como importar dados do Excel?",
            "Quais são os atalhos mais úteis?",
            "Como fazer fitting de curvas?",
            "Como personalizar eixos?"
        ]
        
        for i, question in enumerate(example_questions):
            if st.button(f"📋 {question}", key=f"example_{i}"):
                st.session_state.selected_question = question
    
    # Chat interface
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    # Exibe mensagens
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # Input do usuário
    if prompt := st.chat_input("Faça sua pergunta sobre Origin Software..."):
        process_message(prompt)
    
    # Pergunta selecionada do sidebar
    if "selected_question" in st.session_state:
        process_message(st.session_state.selected_question)
        del st.session_state.selected_question

def process_message(prompt):
    """Processa mensagem do usuário"""
    
    # Verifica API Key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("❌ Configure sua API Key do Claude no menu lateral")
        return
    
    # Adiciona mensagem do usuário
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # Exibe mensagem do usuário
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # Processa resposta
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        try:
            with st.spinner("🤔 Pensando..."):
                # Cria agente
                agent = create_origin_agent()
                if not agent:
                    st.error("❌ Erro ao criar agente")
                    return
                
                # Gera resposta com retry
                response = None
                for attempt in range(3):
                    try:
                        response = agent.run(prompt)
                        break
                    except Exception as e:
                        if "429" in str(e) and attempt < 2:
                            st.warning(f"⏳ Rate limit atingido. Tentativa {attempt + 2}/3 em 2s...")
                            time.sleep(2)
                            continue
                        else:
                            raise e
                
                if not response:
                    st.error("❌ Falha ao gerar resposta após 3 tentativas")
                    return
                
                # Extrai texto da resposta
                if hasattr(response, 'content'):
                    response_text = response.content
                else:
                    response_text = str(response)
                
                # Exibe resposta
                message_placeholder.markdown(response_text)
                
                # Adiciona à sessão
                st.session_state.messages.append({
                    "role": "assistant", 
                    "content": response_text
                })
        
        except Exception as e:
            if "429" in str(e):
                st.error("❌ Limite de requisições excedido. Aguarde alguns minutos e tente novamente.")
                st.info("💡 Dica: O Anthropic tem limites de velocidade para contas novas. Aguarde 5-10 minutos entre requisições.")
            else:
                st.error(f"❌ Erro: {e}")

if __name__ == "__main__":
    # Verifica dependências
    try:
        import agno
        main()
    except ImportError:
        st.error("❌ Instale as dependências: `pip install agno anthropic streamlit`")
        st.stop()