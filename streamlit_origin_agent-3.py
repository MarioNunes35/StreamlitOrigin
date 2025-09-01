
import os
import sys
# Prefer pysqlite3 (bundled SQLite with FTS5) on Streamlit Cloud
try:
    import pysqlite3  # type: ignore
    sys.modules['sqlite3'] = pysqlite3
except Exception:
    pass
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
import streamlit as st
from PyPDF2 import PdfReader

APP_TITLE = "Origin Software Agent"
DEFAULT_DOCS_DIR = "docs/origin"
DATA_DIR = "data"
DOC_DB_PATH = os.path.join(DATA_DIR, "documents.db")
CHAT_DB_PATH = os.path.join(DATA_DIR, "chat.db")
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
TOP_K = 6

def get_anthropic_client():
    api_key = None
    if "ANTHROPIC_API_KEY" in st.secrets:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None, None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        model = st.secrets.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20240620")
        return client, model
    except Exception as e:
        st.warning(f"Falha ao carregar Anthropic: {e}")
        return None, None

def ensure_dirs_and_dbs():
    os.makedirs(DATA_DIR, exist_ok=True)
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
        # Messages API (Anthropic >= 0.2x)
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
    st.title(APP_TITLE)
    st.caption("Assistente (Streamlit Cloud) — PDFs da pasta + SQLite, sem pedir API na interface.")

    ensure_dirs_and_dbs()

    with st.sidebar:
        st.header("Configuração")
        client, model = get_anthropic_client()
        if client:
            st.success(f"Anthropic configurado ({model})")
        else:
            st.info("Configure `ANTHROPIC_API_KEY` em Secrets do Streamlit Cloud.")

        st.header("Indexação de PDFs")
        docs_dir = st.text_input("Pasta com PDFs", value=DEFAULT_DOCS_DIR, help="Arquivos do repo (ex.: docs/origin)")
        if st.button("Indexar/Atualizar banco"):
            with st.spinner("Indexando PDFs..."):
                nd, nc = index_folder(docs_dir)
            st.success(f"Indexação: {nd} documentos, {nc} trechos.")

        st.markdown("---")
        st.subheader("Upload rápido (opcional)")
        st.caption("Armazenamento efêmero no Cloud; uploads servem para a sessão atual.")
        files = st.file_uploader("Adicione PDFs", type=["pdf"], accept_multiple_files=True)
        if files:
            os.makedirs(docs_dir, exist_ok=True)
            saved = 0
            for f in files:
                out = os.path.join(docs_dir, f.name)
                with open(out, "wb") as w:
                    w.write(f.read())
                saved += 1
            st.success(f"{saved} PDF(s) salvo(s) em {docs_dir}. Clique em 'Indexar/Atualizar banco'.")

    if "conv_id" not in st.session_state:
        st.session_state.conv_id = start_conversation("Perguntas sobre Origin")

    st.subheader("Pergunte com base nos PDFs (Questionário)")
    question = st.text_input("Digite sua pergunta:", placeholder="Ex.: Como importar dados do Excel no Origin?")
    if st.button("Responder") and question.strip():
        add_message(st.session_state.conv_id, "user", question.strip())
        rows = search_chunks(question, TOP_K)
        context_chunks = [r[0] for r in rows]
        answer = llm_answer(question.strip(), context_chunks)
        add_message(st.session_state.conv_id, "assistant", answer)

    st.divider()
    st.subheader("Conversas salvas")
    cols = st.columns([3,1])
    with cols[0]:
        convs = list_conversations()
        if convs:
            labels = [f"#{cid} — {title} ({created_at.split('T')[0]})" for cid, title, created_at in convs]
            idx = st.selectbox("Escolha uma conversa:", options=list(range(len(convs))), format_func=lambda i: labels[i])
            st.session_state.conv_id = convs[idx][0]
    with cols[1]:
        if st.button("Nova conversa"):
            st.session_state.conv_id = start_conversation("Nova conversa")
            st.experimental_rerun()

    if st.session_state.conv_id:
        msgs = get_messages(st.session_state.conv_id)
        for role, content, created in msgs:
            with st.chat_message("user" if role == "user" else "assistant"):
                st.markdown(content)

    st.caption("Para persistência real no Cloud, considere sincronizar os .db com GitHub/Gist ou usar DB externo.")

if __name__ == "__main__":
    main()
