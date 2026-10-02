"""Persistencia de transcripciones en SQLite."""
import getpass
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "transcribe.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT NOT NULL,
    file_hash     TEXT,
    size_bytes    INTEGER,
    ext           TEXT,
    input_path    TEXT,
    audio_path    TEXT,
    text          TEXT NOT NULL,
    model         TEXT,
    language      TEXT,
    source        TEXT NOT NULL DEFAULT 'app',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hash ON transcriptions(file_hash);
CREATE INDEX IF NOT EXISTS idx_created ON transcriptions(created_at DESC);

CREATE TABLE IF NOT EXISTS versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id      INTEGER NOT NULL,
    text        TEXT NOT NULL,
    language    TEXT,
    model       TEXT,
    created_at  TEXT NOT NULL,
    replaced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ver_row ON versions(row_id, replaced_at DESC);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    action      TEXT NOT NULL,
    row_id      INTEGER,
    subject     TEXT,
    detail      TEXT,
    origin      TEXT,
    actor       TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_row ON audit(row_id);
"""

# Lo fija la interfaz al arrancar: "gui" o "cli". Sirve para saber desde
# donde se hizo un cambio, que es lo que varia en una app de un solo usuario.
ORIGIN = "?"


def set_origin(nombre):
    global ORIGIN
    ORIGIN = nombre


def log(action, row_id=None, subject=None, detail=None):
    """Registra un cambio. Nunca interrumpe la operacion que lo genero."""
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO audit (at, action, row_id, subject, detail,"
                " origin, actor) VALUES (?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), action, row_id,
                 subject, detail, ORIGIN, getpass.getuser()),
            )
    except Exception:
        pass


def audit(limit=50, row_id=None, action=None):
    sql = "SELECT * FROM audit"
    cond, args = [], []
    if row_id is not None:
        cond.append("row_id = ?")
        args.append(row_id)
    if action:
        cond.append("action = ?")
        args.append(action)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY at DESC, id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_by_hash(file_hash: str):
    """Transcripción previa del mismo audio, o None.

    El hash se calcula sobre el contenido, asi que reconoce un archivo ya
    transcrito aunque llegue con otro nombre. Vale para cualquier origen:
    las filas importadas tambien tienen el hash real de su archivo.
    """
    if not file_hash:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM transcriptions WHERE file_hash = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (file_hash,),
        ).fetchone()
    return dict(row) if row else None


def save(**kw) -> int:
    kw.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    kw.setdefault("source", "app")
    cols = ", ".join(kw)
    marks = ", ".join("?" for _ in kw)
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO transcriptions ({cols}) VALUES ({marks})", tuple(kw.values())
        )
        row_id = cur.lastrowid
    log("crear", row_id, kw.get("filename"),
        f"{kw.get('ext') or '?'} · {len(kw.get('text') or '')} chars")
    return row_id


def update_text(row_id: int, text: str, language=None, model=None):
    """Reemplaza el texto, archivando la version anterior.

    Retranscribir puede dar un resultado peor (idioma equivocado, audio con
    ruido): el texto previo se guarda para poder volver a el.
    """
    ahora = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        anterior = conn.execute(
            "SELECT filename, text, language, model, created_at"
            " FROM transcriptions WHERE id = ?", (row_id,),
        ).fetchone()

        if anterior and anterior["text"]:
            conn.execute(
                "INSERT INTO versions (row_id, text, language, model,"
                " created_at, replaced_at) VALUES (?,?,?,?,?,?)",
                (row_id, anterior["text"], anterior["language"],
                 anterior["model"], anterior["created_at"], ahora),
            )

        campos = ["text = ?", "created_at = ?"]
        args = [text, ahora]
        if language is not None:
            campos.append("language = ?")
            args.append(language)
        if model is not None:
            campos.append("model = ?")
            args.append(model)
        args.append(row_id)
        conn.execute(
            f"UPDATE transcriptions SET {', '.join(campos)} WHERE id = ?",
            tuple(args),
        )

    if anterior:
        log("retranscribir", row_id, anterior["filename"],
            f"{len(anterior['text'] or '')} -> {len(text)} chars"
            " (versión anterior archivada)")


def versions(row_id: int):
    """Versiones archivadas de una fila, de la mas reciente a la mas antigua."""
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM versions WHERE row_id = ?"
            " ORDER BY replaced_at DESC, id DESC", (row_id,),
        ).fetchall()]


def restore_version(version_id: int):
    """Vuelve a una version archivada; la actual se archiva a su vez."""
    with connect() as conn:
        v = conn.execute("SELECT * FROM versions WHERE id = ?",
                         (version_id,)).fetchone()
    if not v:
        return None
    update_text(v["row_id"], v["text"], language=v["language"],
                model=v["model"])
    log("restaurar", v["row_id"], None,
        f"restaurada versión de {v['created_at']}")
    return v["row_id"]


def list_all(search: str = ""):
    sql = "SELECT * FROM transcriptions"
    args = ()
    if search:
        sql += " WHERE filename LIKE ? OR text LIKE ?"
        args = (f"%{search}%", f"%{search}%")
    # El id desempata cuando dos filas comparten timestamp: es estrictamente
    # creciente, asi que refleja el orden real de creacion.
    sql += " ORDER BY created_at DESC, id DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def files_for(row, include_original: bool = False):
    """Rutas en disco que un borrado eliminaria para esta fila.

    Se usa tanto para el aviso de la UI como para el borrado, asi que lo que
    se anuncia y lo que se borra no pueden divergir.
    """
    paths = []
    # Las transcripciones antiguas dejaron un .txt en out/; las nuevas no
    # generan ninguno, pero si existe se borra con la entrada.
    txt = os.path.join(BASE_DIR, "out", row["filename"])
    if os.path.exists(txt):
        paths.append(txt)

    wav, original = row["audio_path"], row["input_path"]
    # En passthrough (.mp3/.m4a) audio_path apunta al original, no a un
    # derivado: ahi no hay ningun .wav que borrar.
    if wav and wav != original and wav.endswith(".wav") and os.path.exists(wav):
        paths.append(wav)

    # Derivados internos en out/, nombrados por el hash del contenido.
    h = row["file_hash"]
    if h:
        for sufijo in (".wav", "_play.m4a"):
            d = os.path.join(BASE_DIR, "out", f"{h[:16]}{sufijo}")
            if os.path.exists(d) and d not in paths:
                paths.append(d)

    if include_original and original and os.path.exists(original) and original not in paths:
        paths.append(original)

    return paths


def delete(row_id: int, remove_files: bool = False, remove_original: bool = False):
    """Borra la fila. Con remove_files, tambien el .txt y el .wav derivados.

    El original en in/ solo se borra con remove_original explicito: puede ser
    el unico ejemplar de ese audio. Devuelve la lista de rutas borradas.
    """
    removed = []
    with connect() as conn:
        # files_for() necesita tambien file_hash para los derivados de out/.
        row = conn.execute(
            "SELECT filename, audio_path, input_path, file_hash"
            " FROM transcriptions WHERE id = ?",
            (row_id,),
        ).fetchone()

        if row and remove_files:
            for path in files_for(row, include_original=remove_original):
                try:
                    os.remove(path)
                    removed.append(path)
                except OSError:
                    pass

        conn.execute("DELETE FROM versions WHERE row_id = ?", (row_id,))
        conn.execute("DELETE FROM transcriptions WHERE id = ?", (row_id,))

    if row:
        borrados = ", ".join(os.path.basename(p) for p in removed) or "ninguno"
        log("eliminar", row_id, row["filename"], f"archivos: {borrados}")
    return removed
