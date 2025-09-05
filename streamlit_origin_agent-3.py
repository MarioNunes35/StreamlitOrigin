
import os
import sys
import hashlib
import sqlite3
import json
from datetime import datetime, timedelta
import streamlit as st

# Prefer pysqlite3 (bundled SQLite with FTS5) on Streamlit Cloud
try:
    import pysqlite3  # type: ignore
    sys.modules['sqlite3'] = pysqlite3
except Exception:
    pass

from PyPDF2 import PdfReader
from pathlib import Path
from typing import List, Tuple, Optional, Dict

APP_TITLE = "Origin Software Agent"
DEFAULT_DOCS_DIR = "docs/origin"
DATA_DIR = "data"
DOC_DB_PATH = os.path.join(DATA_DIR, "documents.db")
CHAT_DB_PATH = os.path.join(DATA_DIR, "chat.db")
USER_DB_PATH = os.path.join(DATA_DIR, "users.db")
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
TOP_K = 6

# =============================================================================
# CLOUDFLARE R2 STORAGE - VERSÃO CORRIGIDA
# =============================================================================

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    from botocore.config import Config
    HAS_S3 = True
except ImportError:
    HAS_S3 = False
    boto3 = None
    ClientError = Exception
    NoCredentialsError = Exception

def get_r2_config() -> Optional[Dict]:
    """Obtém configuração do R2 de forma robusta"""
    if not HAS_S3:
        return None
    
    # Tentar múltiplas formas de obter as configurações
    config = {}
    
    # 1. Primeiro tenta st.secrets
    try:
        if hasattr(st, 'secrets'):
            config['endpoint'] = st.secrets.get("S3_ENDPOINT_URL", "")
            config['bucket'] = st.secrets.get("S3_BUCKET", "")
            config['access_key'] = st.secrets.get("AWS_ACCESS_KEY_ID", "")
            config['secret_key'] = st.secrets.get("AWS_SECRET_ACCESS_KEY", "")
            config['region'] = st.secrets.get("S3_REGION", "auto")
    except Exception:
        pass
    
    # 2. Se algum valor estiver vazio, tenta variáveis de ambiente
    if not config.get('endpoint'):
        config['endpoint'] = os.getenv("S3_ENDPOINT_URL", "")
    if not config.get('bucket'):
        config['bucket'] = os.getenv("S3_BUCKET", "")
    if not config.get('access_key'):
        config['access_key'] = os.getenv("AWS_ACCESS_KEY_ID", "")
    if not config.get('secret_key'):
        config['secret_key'] = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    if not config.get('region'):
        config['region'] = os.getenv("S3_REGION", "auto")
    
    # Validar se todas as configurações necessárias estão presentes
    required = ['endpoint', 'bucket', 'access_key', 'secret_key']
    for key in required:
        if not config.get(key):
            return None
    
    return config

def get_r2_client():
    """Configura cliente R2 com tratamento de erros melhorado"""
    if not HAS_S3:
        return None
    
    config = get_r2_config()
    if not config:
        return None
    
    try:
        # Configuração específica para R2
        boto_config = Config(
            signature_version='s3v4',
            retries={'max_attempts': 3, 'mode': 'standard'}
        )
        
        client = boto3.client(
            "s3",
            endpoint_url=config['endpoint'],
            aws_access_key_id=config['access_key'],
            aws_secret_access_key=config['secret_key'],
            region_name=config.get('region', 'auto'),
            config=boto_config
        )
        
        # Testar conexão
        try:
            client.head_bucket(Bucket=config['bucket'])
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == '404':
                # Bucket não existe, tentar criar
                try:
                    client.create_bucket(Bucket=config['bucket'])
                except Exception:
                    pass
            elif error_code != '403':  # 403 pode significar que existe mas sem permissão de head
                return None
        
        return {
            "client": client, 
            "bucket": config['bucket'], 
            "prefix": "origin-agent/"
        }
        
    except Exception as e:
        print(f"[R2] Erro ao conectar: {str(e)}")
        return None

def r2_key(filename: str) -> str:
    """Gera chave do R2 com prefixo"""
    r2 = get_r2_client()
    prefix = r2["prefix"] if r2 else "origin-agent/"
    return f"{prefix}{filename}"

def backup_to_r2():
    """Faz backup dos DBs para R2"""
    r2 = get_r2_client()
    if not r2:
        return False
    
    client = r2["client"]
    bucket = r2["bucket"]
    
    success = True
    for db_file in [USER_DB_PATH, DOC_DB_PATH, CHAT_DB_PATH]:
        if os.path.exists(db_file):
            try:
                key = r2_key(os.path.basename(db_file))
                client.upload_file(db_file, bucket, key)
                print(f"[R2] Backup: {db_file} -> {key}")
            except Exception as e:
                print(f"[R2] Erro no backup {db_file}: {str(e)}")
                success = False
    
    return success

def restore_from_r2():
    """Restaura DBs do R2"""
    r2 = get_r2_client()
    if not r2:
        return False
    
    client = r2["client"]
    bucket = r2["bucket"]
    
    success = False
    for db_file in [USER_DB_PATH, DOC_DB_PATH, CHAT_DB_PATH]:
        if not os.path.exists(db_file):
            try:
                key = r2_key(os.path.basename(db_file))
                client.download_file(bucket, key, db_file)
                print(f"[R2] Restaurado: {key} -> {db_file}")
                success = True
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code == 'NoSuchKey':
                    print(f"[R2] Arquivo não existe: {key}")
                else:
                    print(f"[R2] Erro ao restaurar {key}: {str(e)}")
            except Exception as e:
                print(f"[R2] Erro genérico ao restaurar {key}: {str(e)}")
    
    return success

def sync_user_data():
    """Sincroniza dados do usuário com R2 automaticamente"""
    if get_r2_client():
        backup_to_r2()

def test_r2_connection():
    """Testa e retorna status detalhado da conexão R2"""
    if not HAS_S3:
        return {"status": "error", "message": "boto3 não instalado"}
    
    config = get_r2_config()
    if not config:
        missing = []
        test_config = get_r2_config() or {}
        for key in ['endpoint', 'bucket', 'access_key', 'secret_key']:
            if not test_config.get(key):
                missing.append(key.upper())
        return {
            "status": "error", 
            "message": f"Configurações faltando: {', '.join(missing)}"
        }
    
    # Tentar conectar
    r2 = get_r2_client()
    if r2:
        return {
            "status": "success",
            "message": "Conectado com sucesso",
            "bucket": config['bucket'],
            "endpoint": config['endpoint']
        }
    else:
        return {
            "status": "error",
            "message": "Falha na conexão - verifique as credenciais"
        }

# =============================================================================
# SISTEMA DE AUTENTICAÇÃO
# =============================================================================

def hash_password(password: str) -> str:
    """Cria hash seguro da senha"""
    return hashlib.sha256(password.encode()).hexdigest()

def create_user_db():
    """Cria tabela de usuários"""
    with sqlite3.connect(USER_DB_PATH) as con:
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT,
                created_at TEXT,
                last_login TEXT,
                active INTEGER DEFAULT 1,
                subscription_expires TEXT
            );
        """)
        
        # Criar usuário admin padrão se não existir
        cur.execute("SELECT id FROM users WHERE username = 'admin'")
        if not cur.fetchone():
            admin_pass = hash_password("admin123")  # MUDE ESTA SENHA!
            cur.execute("""
                INSERT INTO users(username, password_hash, email, created_at, active, subscription_expires)
                VALUES(?, ?, ?, ?, 1, ?)
            """, ("admin", admin_pass, "admin@origin.com", 
                  datetime.utcnow().isoformat(),
                  (datetime.utcnow() + timedelta(days=365)).isoformat()))
        
        con.commit()
    
    # Backup automático para R2
    sync_user_data()

def validate_user(username: str, password: str) -> bool:
    """Valida credenciais do usuário"""
    if not username or not password:
        return False
    
    with sqlite3.connect(USER_DB_PATH) as con:
        cur = con.cursor()
        cur.execute("""
            SELECT password_hash, active, subscription_expires 
            FROM users WHERE username = ?
        """, (username,))
        result = cur.fetchone()
        
        if not result:
            return False
        
        password_hash, active, subscription_expires = result
        
        # Verificar senha
        if hash_password(password) != password_hash:
            return False
        
        # Verificar se usuário está ativo
        if not active:
            st.error("Usuário desativado. Entre em contato com o suporte.")
            return False
        
        # Verificar se assinatura não expirou
        if subscription_expires:
            expiry = datetime.fromisoformat(subscription_expires)
            if datetime.utcnow() > expiry:
                st.error("Assinatura expirada. Renove seu acesso.")
                return False
        
        # Atualizar último login
        cur.execute(
            "UPDATE users SET last_login = ? WHERE username = ?",
            (datetime.utcnow().isoformat(), username)
        )
        con.commit()
        
        # Backup após atualização
        sync_user_data()
        
        return True

def add_user(username: str, password: str, email: str = "", months: int = 12) -> bool:
    """Adiciona novo usuário (apenas admin)"""
    if 'username' not in st.session_state or st.session_state.username != 'admin':
        return False
    
    with sqlite3.connect(USER_DB_PATH) as con:
        cur = con.cursor()
        try:
            expiry = datetime.utcnow() + timedelta(days=30*months)
            cur.execute("""
                INSERT INTO users(username, password_hash, email, created_at, active, subscription_expires)
                VALUES(?, ?, ?, ?, 1, ?)
            """, (username, hash_password(password), email, 
                  datetime.utcnow().isoformat(), expiry.isoformat()))
            con.commit()
            
            # Backup após criação
            sync_user_data()
            return True
        except sqlite3.IntegrityError:
            return False

def list_users():
    """Lista usuários (apenas admin)"""
    if 'username' not in st.session_state or st.session_state.username != 'admin':
        return []
    
    with sqlite3.connect(USER_DB_PATH) as con:
        cur = con.cursor()
        cur.execute("""
            SELECT username, email, created_at, last_login, active, subscription_expires
            FROM users ORDER BY created_at DESC
        """)
        return cur.fetchall()

def show_login_page():
    """Página de login"""
    st.markdown("""
    <div style="text-align: center; padding: 2rem;">
        <h1>🔐 Origin Software Agent</h1>
        <p>Entre com suas credenciais para acessar o sistema</p>
    </div>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        with st.container():
            st.markdown("### Login")
            
            username = st.text_input("👤 Usuário", placeholder="Digite seu usuário", key="login_username")
            password = st.text_input("🔑 Senha", type="password", placeholder="Digite sua senha", key="login_password")
            
            col_login, col_demo = st.columns(2)
            
            with col_login:
                if st.button("🚀 Entrar", use_container_width=True, key="login_button"):
                    if validate_user(username, password):
                        st.session_state.authenticated = True
                        st.session_state.username = username
                        st.success("Login realizado com sucesso!")
                        st.rerun()
                    else:
                        st.error("❌ Usuário ou senha incorretos")
            
            with col_demo:
                if st.button("👁️ Demo", use_container_width=True, key="demo_button"):
                    st.info("""
                    **Conta Demo:**
                    - Usuário: `admin`
                    - Senha: `admin123`
                    """)

def show_admin_panel():
    """Painel administrativo com diagnóstico melhorado"""
    if st.session_state.username != 'admin':
        return
    
    st.subheader("🛠️ Painel Administrativo")
    
    # Status detalhado do R2
    r2_status = test_r2_connection()
    
    if r2_status['status'] == 'success':
        st.success(f"☁️ Cloudflare R2 conectado - Bucket: {r2_status['bucket']}")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("📤 Backup Manual", key="manual_backup"):
                with st.spinner("Fazendo backup..."):
                    if backup_to_r2():
                        st.success("✅ Backup realizado!")
                    else:
                        st.error("❌ Erro no backup")
        
        with col2:
            if st.button("📥 Restaurar", key="manual_restore"):
                with st.spinner("Restaurando..."):
                    if restore_from_r2():
                        st.success("✅ Restaurado!")
                        st.rerun()
                    else:
                        st.warning("⚠️ Nenhum backup encontrado")
        
        with col3:
            if st.button("🔄 Testar Conexão", key="test_connection"):
                st.rerun()
    else:
        st.error(f"❌ R2 não conectado: {r2_status['message']}")
        
        # Mostrar instruções de configuração
        with st.expander("📋 Como configurar o R2"):
            st.markdown("""
            ### Configuração no Streamlit Cloud:
            
            1. **Vá para Settings > Secrets** no seu app
            2. **Adicione as seguintes variáveis:**
            
            ```toml
            S3_ENDPOINT_URL = "https://[seu-account-id].r2.cloudflarestorage.com"
            S3_BUCKET = "seu-bucket-name"
            AWS_ACCESS_KEY_ID = "sua-access-key"
            AWS_SECRET_ACCESS_KEY = "sua-secret-key"
            S3_REGION = "auto"
            ```
            
            3. **Onde encontrar essas informações:**
               - Acesse o dashboard do Cloudflare
               - Vá para R2 Object Storage
               - Crie um bucket se não tiver
               - Em "Manage R2 API Tokens", crie um token
               - Use as credenciais geradas
            
            4. **Reinicie o app após adicionar os secrets**
            """)
            
            # Debug info para admin
            if st.checkbox("🔍 Mostrar diagnóstico detalhado"):
                st.code(f"""
                Configurações detectadas:
                - boto3 instalado: {HAS_S3}
                - Método de config: {'st.secrets' if hasattr(st, 'secrets') else 'env vars'}
                
                Valores encontrados (parcial):
                - S3_ENDPOINT_URL: {'✓' if get_r2_config() and get_r2_config().get('endpoint') else '✗'}
                - S3_BUCKET: {'✓' if get_r2_config() and get_r2_config().get('bucket') else '✗'}
                - AWS_ACCESS_KEY_ID: {'✓' if get_r2_config() and get_r2_config().get('access_key') else '✗'}
                - AWS_SECRET_ACCESS_KEY: {'✓' if get_r2_config() and get_r2_config().get('secret_key') else '✗'}
                """)
    
    st.divider()
    
    # Gerenciamento de usuários
    tab1, tab2 = st.tabs(["👥 Usuários", "➕ Adicionar Usuário"])
    
    with tab1:
        users = list_users()
        if users:
            st.markdown("### Usuários Cadastrados")
            for user in users:
                username, email, created, last_login, active, expires = user
                
                with st.expander(f"👤 {username} {'✅' if active else '❌'}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**Email:** {email or 'Não informado'}")
                        st.write(f"**Criado:** {created[:10] if created else 'N/A'}")
                    with col2:
                        st.write(f"**Último login:** {last_login[:10] if last_login else 'Nunca'}")
                        st.write(f"**Expira:** {expires[:10] if expires else 'Nunca'}")
    
    with tab2:
        st.markdown("### Adicionar Novo Usuário")
        
        new_username = st.text_input("Nome de usuário", key="admin_new_username")
        new_password = st.text_input("Senha", type="password", key="admin_new_password")
        new_email = st.text_input("Email (opcional)", key="admin_new_email")
        new_months = st.number_input("Meses de acesso", min_value=1, max_value=36, value=12, key="admin_new_months")
        
        if st.button("Criar Usuário", key="admin_create_user"):
            if new_username and new_password:
                if add_user(new_username, new_password, new_email, new_months):
                    st.success(f"✅ Usuário '{new_username}' criado com sucesso!")
                    st.rerun()
                else:
                    st.error("❌ Erro ao criar usuário (nome já existe?)")
            else:
                st.error("❌ Usuário e senha são obrigatórios")

# =============================================================================
# FUNÇÕES ORIGINAIS (mantidas)
# =============================================================================

def get_anthropic_client():
    """Obtém cliente Anthropic de forma robusta"""
    api_key = None
    
    # Tentar várias formas de obter a API key
    try:
        if hasattr(st, 'secrets') and "ANTHROPIC_API_KEY" in st.secrets:
            api_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    
    if not api_key:
        return None, None
    
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        
        # Obter modelo
        model = "claude-3-5-sonnet-20240620"  # Modelo padrão
        try:
            if hasattr(st, 'secrets') and "ANTHROPIC_MODEL" in st.secrets:
                model = st.secrets["ANTHROPIC_MODEL"]
        except Exception:
            model = os.environ.get("ANTHROPIC_MODEL", model)
        
        return client, model
    except Exception as e:
        st.warning(f"Falha ao carregar Anthropic: {e}")
        return None, None

def ensure_dirs_and_dbs():
    """Cria diretórios e bancos de dados necessários"""
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Primeiro tenta restaurar do R2
    restore_from_r2()
    
    # Depois cria estruturas se necessário
    create_user_db()
    
    with sqlite3.connect(DOC_DB_PATH) as con:
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents(
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                n_pages INTEGER,
                added_at TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                chunk_index INTEGER,
                text TEXT,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            );
        """)
        try:
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(text, content='', tokenize='porter');
            """)
            con.commit()
        except sqlite3.OperationalError:
            pass
    
    with sqlite3.connect(CHAT_DB_PATH) as con:
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations(
                id INTEGER PRIMARY KEY,
                title TEXT,
                created_at TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY,
                conversation_id INTEGER,
                role TEXT,
                content TEXT,
                created_at TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            );
        """)
        con.commit()

def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    parts = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + size)
        parts.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
        if end == n:
            break
    return [p.strip() for p in parts if p.strip()]

def extract_text_from_pdf(pdf_path: str):
    try:
        reader = PdfReader(pdf_path)
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n".join(pages), len(reader.pages)
    except Exception as e:
        st.error(f"Erro ao ler {pdf_path}: {e}")
        return "", 0

def index_folder(folder: str):
    folder = folder.strip()
    if not os.path.isdir(folder):
        st.error(f"Pasta não encontrada: {folder}")
        return 0, 0
    new_docs, new_chunks = 0, 0
    with sqlite3.connect(DOC_DB_PATH) as con:
        cur = con.cursor()
        pdf_files = [str(p) for p in Path(folder).rglob("*.pdf")]
        for path in pdf_files:
            filename = os.path.basename(path)
            cur.execute("SELECT id FROM documents WHERE path = ?", (path,))
            if cur.fetchone():
                continue
            text, n_pages = extract_text_from_pdf(path)
            if not text.strip():
                continue
            cur.execute(
                "INSERT INTO documents(filename, path, n_pages, added_at) VALUES(?,?,?,?)",
                (filename, path, n_pages, datetime.utcnow().isoformat())
            )
            doc_id = cur.lastrowid
            for i, chunk in enumerate(split_text(text)):
                cur.execute(
                    "INSERT INTO chunks(document_id, chunk_index, text) VALUES(?,?,?)",
                    (doc_id, i, chunk)
                )
                try:
                    cur.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)", (cur.lastrowid, chunk))
                except sqlite3.OperationalError:
                    pass
                new_chunks += 1
            new_docs += 1
        con.commit()
    
    # Backup após indexação
    backup_to_r2()
    return new_docs, new_chunks

def search_chunks(query: str, top_k: int = TOP_K):
    query = query.strip()
    if not query:
        return []
    with sqlite3.connect(DOC_DB_PATH) as con:
        cur = con.cursor()
        try:
            cur.execute("""
                SELECT c.text, c.document_id, d.filename, bm25(chunks_fts)
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                JOIN documents d ON d.id = c.document_id
                WHERE chunks_fts MATCH ?
                ORDER BY bm25(chunks_fts) LIMIT ?
            """, (query, top_k))
            rows = cur.fetchall()
            if rows:
                return rows
        except sqlite3.OperationalError:
            pass
        like = f"%{query}%"
        cur.execute("""
            SELECT c.text, c.document_id, d.filename, 0.0
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.text LIKE ?
            LIMIT ?
        """, (like, top_k))
        return cur.fetchall()

def start_conversation(title: str = "Nova conversa"):
    with sqlite3.connect(CHAT_DB_PATH) as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO conversations(title, created_at) VALUES(?, ?)",
            (title, datetime.utcnow().isoformat())
        )
        con.commit()
        # Backup após nova conversa
        backup_to_r2()
        return cur.lastrowid

def add_message(conversation_id: int, role: str, content: str):
    with sqlite3.connect(CHAT_DB_PATH) as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) VALUES(?,?,?,?)",
            (conversation_id, role, content, datetime.utcnow().isoformat())
        )
        con.commit()

def list_conversations():
    with sqlite3.connect(CHAT_DB_PATH) as con:
        cur = con.cursor()
        cur.execute("SELECT id, title, created_at FROM conversations ORDER BY id DESC")
        return cur.fetchall()

def get_messages(conversation_id: int):
    with sqlite3.connect(CHAT_DB_PATH) as con:
        cur = con.cursor()
        cur.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,)
        )
        return cur.fetchall()

def llm_answer(question: str, context_chunks: List[str]) -> str:
    client, model = get_anthropic_client()
    system_prompt = (
        "Você é um assistente técnico. Responda de forma direta e cite trechos do contexto quando possível. "
        "Se a informação não estiver nos documentos, diga que não encontrou."
    )
    context_text = "\n\n".join([f"[Trecho {i+1}] {t}" for i, t in enumerate(context_chunks)])
    if not client:
        return "⚠️ Nenhuma API key Anthropic encontrada. Trechos relevantes:\n\n" + context_text
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=800,
            temperature=0.1,
            system=system_prompt + "\nUse EXCLUSIVAMENTE o contexto fornecido.",
            messages=[
                {
                    "role": "user",
                    "content": f"Pergunta: {question}\n\nContexto:\n{context_text}"
                }
            ],
        )
        if hasattr(msg, "content"):
            parts = []
            for block in msg.content:
                if getattr(block, "type", "") == "text":
                    parts.append(block.text)
            return "".join(parts).strip() or "(sem resposta)"
        return str(msg)
    except Exception as e:
        return f"Falha ao chamar Anthropic: {e}\n\n{context_text}"

def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🧪", layout="wide")
    
    ensure_dirs_and_dbs()
    
    # Verificar autenticação
    if 'authenticated' not in st.session_state or not st.session_state.authenticated:
        show_login_page()
        return
    
    # Interface principal (usuário autenticado)
    st.title(f"🧪 {APP_
