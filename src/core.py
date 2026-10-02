"""Nucleo compartido: transcripcion, conversion y nombres.

Sin dependencias de UI. Lo usan por igual el CLI (puentes maquina-maquina)
y la interfaz grafica.
"""
import os
import re
import subprocess
import unicodedata
from datetime import datetime

import db

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}

# Sufijo que anadia la version anterior de la app, y fechas embebidas por
# origen ("WhatsApp Video 2026-09-13 at 11.36.23"): ninguno aporta al slug.
_STAMP = re.compile(r"_?\d{8}_\d{6}")
_FECHA_ORIGEN = re.compile(r"\d{4}-\d{2}-\d{2}(\s+at\s+[\d.]+)?", re.I)


def slugify(name):
    """Nombre legible en minusculas con guiones, sin fechas ni ruido.

    No elimina palabras genericas como "audio": si un nombre es solo eso,
    vale mas conservarlo que dejar la entrada sin titulo.
    """
    s = _STAMP.sub("", name)
    s = _FECHA_ORIGEN.sub("", s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    # Numeracion larga de WhatsApp (00000127-AUDIO-...) y horas sueltas que
    # quedan tras quitar la fecha ("at 2.45.12 pm" -> "2-45-12-pm").
    s = re.sub(r"\b\d{6,}\b", "", s)
    s = re.sub(r"\b\d{1,2}[.:-]\d{2}([.:-]\d{2})?\s*(am|pm)?\b", "", s)
    s = re.sub(r"\b(am|pm|at)\b", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-") or "sin-titulo"

BASE_DIR = db.BASE_DIR
IN_DIR = os.path.join(BASE_DIR, "in")
OUT_DIR = os.path.join(BASE_DIR, "out")
KEY_PATH = os.path.join(BASE_DIR, "key.env")

MODEL = "openai/whisper-large-v3"
# Formatos que la API acepta tal cual; el resto se convierte con ffmpeg.
PASSTHROUGH = [".mp3", ".wav", ".flac", ".m4a"]
UPLOAD_EXTS = [
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".flac",
    ".m4a", ".ogg", ".oga", ".opus", ".aac", ".weba", ".amr", ".3gp", ".wma",
]

IDIOMAS = {
    "Detectar automáticamente": None,
    "Español": "es",
    "Inglés": "en",
    "Alemán": "de",
    "Portugués": "pt",
    "Francés": "fr",
    "Italiano": "it",
    "Quechua": "qu",
}


class TranscribeError(RuntimeError):
    """Fallo esperable: ffmpeg ausente, archivo ilegible, API caida."""


def get_api_key():
    if os.path.exists(KEY_PATH):
        key = open(KEY_PATH).read().strip()
        if key:
            return key
    key = os.environ.get("TOGETHER_API_KEY")
    if not key:
        raise TranscribeError(
            "API Key de Together no encontrada ('key.env' o TOGETHER_API_KEY)."
        )
    return key


def extract_audio(src, dest):
    """Extrae audio a WAV 16kHz mono."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", dest],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise TranscribeError("ffmpeg no está instalado o no está en el PATH.")
    except subprocess.CalledProcessError as e:
        raise TranscribeError(f"ffmpeg falló al procesar el archivo: {e}")
    return dest


def build_name(original_name, ext, when=None):
    """<tipo>_<AAAAMMDD-HHMM>_<slug>, sin colisiones en in/.

    Sin sufijo de formato: la extension del archivo ya lo indica.
    """
    tipo = "video" if ext in VIDEO_EXTS else "audio"
    fecha = (when or datetime.now()).strftime("%Y%m%d-%H%M")
    slug = slugify(os.path.splitext(original_name)[0])
    slug += _sufijo_por_titulo(slug)
    base = f"{tipo}_{fecha}_{slug}"

    candidate, n = base, 2
    while os.path.exists(os.path.join(IN_DIR, candidate + ext)):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _sufijo_por_titulo(slug, excluir_id=None):
    """Sufijo numerico para un titulo repetido: el mas reciente, el mayor.

    Devuelve "" si el titulo es unico. Se compara el slug, no el nombre
    completo, porque dos entradas con el mismo titulo y distinta fecha son
    justo el caso que hay que desambiguar.
    """
    hermanas = []
    for r in db.list_all():
        if excluir_id is not None and r["id"] == excluir_id:
            continue
        base = os.path.splitext(r["filename"])[0]
        partes = base.split("_")
        otro = "_".join(partes[2:]) if len(partes) >= 3 else base
        # Un slug ya numerado ("informe-2") cuenta como el mismo titulo.
        raiz = re.sub(r"-\d+$", "", otro)
        if raiz == slug:
            hermanas.append((r["created_at"], otro))

    if not hermanas:
        return ""
    usados = set()
    for _, otro in hermanas:
        m = re.search(r"-(\d+)$", otro)
        usados.add(int(m.group(1)) if m else 1)
    return f"-{max(usados) + 1}"


def renombrar_fila(row, titulo):
    """Cambia el titulo de una entrada y su archivo en in/.

    Solo se edita el slug: el prefijo <tipo>_<fecha> se conserva para que el
    orden cronologico y el esquema sigan intactos. Los derivados de out/ no
    se tocan: van por hash, no por nombre.
    """
    nuevo_slug = slugify(titulo)
    if not nuevo_slug or nuevo_slug == "sin-titulo":
        raise TranscribeError("El título no puede quedar vacío.")

    base = os.path.splitext(row["filename"])[0]
    partes = base.split("_")
    if len(partes) >= 3 and partes[0] in ("audio", "video"):
        prefijo = f"{partes[0]}_{partes[1]}"
    else:
        # Nombre fuera del esquema: se reconstruye a partir de la fila.
        tipo = "video" if (row["ext"] or "") in VIDEO_EXTS else "audio"
        fecha = row["created_at"][:16].replace("-", "").replace("T", "-").replace(":", "")
        prefijo = f"{tipo}_{fecha}"

    nuevo_slug += _sufijo_por_titulo(nuevo_slug, excluir_id=row["id"])
    nuevo_base = f"{prefijo}_{nuevo_slug}"
    if nuevo_base == base:
        return row["input_path"], row["filename"]

    original = row["input_path"]
    nuevo_input = original
    if original and os.path.exists(original):
        ext = os.path.splitext(original)[1]
        destino = os.path.join(IN_DIR, nuevo_base + ext)
        n = 2
        while os.path.exists(destino):
            destino = os.path.join(IN_DIR, f"{nuevo_base}-{n}{ext}")
            n += 1
        os.rename(original, destino)
        nuevo_input = destino
        nuevo_base = os.path.splitext(os.path.basename(destino))[0]

    nuevo_filename = nuevo_base + ".txt"
    with db.connect() as conn:
        conn.execute(
            "UPDATE transcriptions SET filename = ?, input_path = ?,"
            " audio_path = ? WHERE id = ?",
            (nuevo_filename, nuevo_input,
             # En passthrough audio_path apunta al propio original.
             nuevo_input if row["audio_path"] == original else row["audio_path"],
             row["id"]),
        )
    db.log("renombrar", row["id"], nuevo_filename,
           f"{os.path.splitext(row['filename'])[0]} -> {os.path.splitext(nuevo_filename)[0]}")
    return nuevo_input, nuevo_filename


def transcribe_file(path, client, language=None, keep_in_place=False,
                    original_name=None):
    """Transcribe un archivo del disco y guarda la fila en la BD.

    keep_in_place deja el archivo donde esta (uso tipico del CLI sobre rutas
    externas); si no, lo copia a in/ con el nombre normalizado.
    Devuelve (fila_id, texto, reusado).
    """
    if not os.path.exists(path):
        raise TranscribeError(f"No se encontró '{path}'")

    with open(path, "rb") as f:
        data = f.read()
    file_hash = db.hash_bytes(data)

    previous = db.find_by_hash(file_hash)
    if previous:
        return previous["id"], previous["text"], True

    ext = os.path.splitext(path)[1].lower()
    base_name = build_name(original_name or os.path.basename(path), ext)

    if keep_in_place:
        input_path = path
    else:
        input_path = os.path.join(IN_DIR, base_name + ext)
        os.makedirs(IN_DIR, exist_ok=True)
        with open(input_path, "wb") as f:
            f.write(data)

    if ext in PASSTHROUGH:
        audio_path = input_path
    else:
        os.makedirs(OUT_DIR, exist_ok=True)
        # out/ es almacen interno: los derivados se nombran por el hash del
        # contenido, asi renombrar el original no obliga a renombrarlos.
        audio_path = extract_audio(
            input_path, os.path.join(OUT_DIR, f"{file_hash[:16]}.wav")
        )

    try:
        with open(audio_path, "rb") as f:
            kw = {"language": language} if language else {}
            res = client.audio.transcriptions.create(model=MODEL, file=f, **kw)
    except Exception as e:
        raise TranscribeError(f"Error durante la transcripción: {e}")

    detected = getattr(res, "language", None) or language
    row_id = db.save(
        filename=base_name + ".txt", file_hash=file_hash,
        size_bytes=os.path.getsize(input_path), ext=ext,
        input_path=input_path, audio_path=audio_path, text=res.text,
        model=MODEL, language=detected,
    )
    return row_id, res.text, False


def retranscribe_row(row, client, language=None):
    """Regenera la transcripcion de una fila desde su audio en disco."""
    path = row["audio_path"] or row["input_path"]
    if not path or not os.path.exists(path):
        raise TranscribeError("El audio ya no está en disco.")

    ext = os.path.splitext(path)[1].lower()
    if ext not in PASSTHROUGH and ext != ".wav":
        path = extract_audio(path, derivado(row, ".wav"))

    try:
        with open(path, "rb") as f:
            kw = {"language": language} if language else {}
            res = client.audio.transcriptions.create(model=MODEL, file=f, **kw)
    except Exception as e:
        raise TranscribeError(f"Error durante la transcripción: {e}")

    detectado = getattr(res, "language", None) or language
    db.update_text(row["id"], res.text, language=detectado, model=MODEL)
    return res.text


# El backend multimedia de macOS (AVFoundation) no decodifica VP9 ni AV1, asi
# que un .webm de YouTube se queda cargando. Para esos se usa el .wav extraido.
CODECS_NO_SOPORTADOS = {"vp9", "vp8", "av1", "theora"}


_CODEC_CACHE = {}


def video_codec(path):
    """Codec de video del archivo, o None si no se puede determinar.

    Cacheado: se consulta en cada seleccion de la lista y lanzar ffprobe
    cada vez frena la interfaz.
    """
    try:
        clave = (path, os.path.getmtime(path))
    except OSError:
        return None
    if clave in _CODEC_CACHE:
        return _CODEC_CACHE[clave]
    resultado = _video_codec_sin_cache(path)
    _CODEC_CACHE[clave] = resultado
    return resultado


def _video_codec_sin_cache(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        return (out.stdout or "").strip().lower() or None
    except Exception:
        return None


def extraer_audio_reproducible(src, dest=None):
    """Extrae audio en estereo y calidad de escucha (AAC .m4a).

    Distinto del .wav de transcripcion, que es mono 16kHz porque es lo que
    Whisper consume. Este es para oirlo: conserva los canales del origen.
    """
    if not os.path.exists(src):
        raise TranscribeError(f"No se encontró '{src}'")

    if dest is None:
        stem = os.path.splitext(os.path.basename(src))[0]
        dest = os.path.join(OUT_DIR, f"{stem}_audio.m4a")

    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vn",
             "-c:a", "aac", "-b:a", "192k", dest],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise TranscribeError("ffmpeg no está instalado o no está en el PATH.")
    except subprocess.CalledProcessError as e:
        raise TranscribeError(f"ffmpeg falló al extraer el audio: {e}")
    return dest


def audio_reproducible(row):
    """Ruta del audio de escucha de esta fila, generandolo si hace falta.

    Devuelve None si no hay origen del que extraerlo.
    """
    original = row["input_path"]
    if not original or not os.path.exists(original):
        return None

    dest = derivado(row, "_play.m4a")
    if os.path.exists(dest):
        return dest
    try:
        return extraer_audio_reproducible(original, dest)
    except TranscribeError:
        return None


def derivado(row, sufijo):
    """Ruta de un archivo derivado en out/, nombrado por el hash de la fila.

    Con filas antiguas sin hash se cae al nombre del .wav asociado, para no
    perder la referencia a lo que ya estaba generado.
    """
    h = row["file_hash"]
    if h:
        return os.path.join(OUT_DIR, f"{h[:16]}{sufijo}")
    base = os.path.splitext(os.path.basename(row["filename"]))[0]
    return os.path.join(OUT_DIR, f"{base}{sufijo}")


def video_resolucion(path):
    """(ancho, alto) del video, o (None, None) si no se puede leer."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
            capture_output=True, text=True, timeout=10,
        )
        w, h = (out.stdout or "").strip().split("x")
        return int(w), int(h)
    except Exception:
        return None, None


def necesita_conversion(path):
    """True si el video existe pero su codec no lo reproduce el sistema."""
    if not path or not os.path.exists(path):
        return False
    if os.path.splitext(path)[1].lower() not in VIDEO_EXTS:
        return False
    return video_codec(path) in CODECS_NO_SOPORTADOS


def convertir_a_mp4(src, dest=None, altura=None, bitrate="1500k",
                    progreso=None):
    """Recodifica un video a H.264/AAC, reproducible en cualquier parte.

    Usa el codificador por hardware de macOS (videotoolbox) y cae al de
    software si no esta disponible. `altura` reescala (720 para 720p);
    None conserva la resolucion. Devuelve la ruta del archivo generado.
    """
    if not os.path.exists(src):
        raise TranscribeError(f"No se encontró '{src}'")

    if dest is None:
        base = os.path.splitext(src)[0]
        dest = f"{base}_h264.mp4"
        n = 2
        while os.path.exists(dest):
            dest = f"{base}_h264-{n}.mp4"
            n += 1

    escala = ["-vf", f"scale=-2:{altura}"] if altura else []
    for encoder in ("h264_videotoolbox", "libx264"):
        cmd = ["ffmpeg", "-y", "-i", src, *escala,
               "-c:v", encoder, "-b:v", bitrate,
               "-c:a", "aac", "-b:a", "128k", dest]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            raise TranscribeError("ffmpeg no está instalado o no está en el PATH.")
        if proc.returncode == 0 and os.path.getsize(dest) > 0:
            db.log("convertir", None, os.path.basename(dest),
                   f"{os.path.basename(src)} -> {encoder}"
                   + (f" {altura}p" if altura else ""))
            return dest
        if progreso:
            progreso(f"{encoder} falló, probando alternativa…")

    raise TranscribeError("ffmpeg no pudo convertir el archivo.")


def playable_media(row):
    """(ruta, es_video) del medio a mostrar, o (None, False).

    Prefiere el video original, salvo que su codec no sea reproducible por el
    sistema: en ese caso cae al .wav extraido, que siempre lo es.
    """
    original = row["input_path"]
    wav = row["audio_path"]
    tiene_wav = bool(wav) and os.path.exists(wav) and wav.endswith(".wav")

    if original and os.path.exists(original):
        ext = os.path.splitext(original)[1].lower()
        if ext in VIDEO_EXTS:
            if video_codec(original) in CODECS_NO_SOPORTADOS:
                # Sin imagen posible: al menos que el audio suene como el
                # original, no el mono 16kHz que se manda a transcribir.
                # Solo si ya esta extraido: generarlo aqui bloquearia la UI.
                estereo = derivado(row, "_play.m4a")
                if os.path.exists(estereo):
                    return estereo, False
                return (wav, False) if tiene_wav else (original, False)
            return original, True
        # Audio original (.mp3, .m4a, .opus...): ya tiene sus canales
        return original, False

    if tiene_wav:
        return wav, False
    return None, False
