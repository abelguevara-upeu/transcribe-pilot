"""Transcribe archivos de audio/video usando Together API (Whisper v3)."""
import os
import subprocess
import sys
from datetime import datetime
from together import Together

if len(sys.argv) < 2:
    print("Uso: python src/transcribe_video.py <nombre_del_video>")
    sys.exit(1)

# Rutas y configuración
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_DIR = os.path.join(BASE_DIR, "in")
OUT_DIR = os.path.join(BASE_DIR, "out")
KEY_PATH = os.path.join(BASE_DIR, "key.env")

os.makedirs(IN_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

raw_input = sys.argv[1]
video_path = raw_input if os.path.isabs(raw_input) or os.path.dirname(raw_input) else os.path.join(IN_DIR, raw_input)

if not os.path.exists(video_path):
    print(f"Error: No se encontró '{video_path}'")
    sys.exit(1)

api_key = open(KEY_PATH).read().strip() if os.path.exists(KEY_PATH) else os.environ.get("TOGETHER_API_KEY")

if not api_key:
    print("Error: API Key de Together no encontrada.")
    sys.exit(1)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
base_name = os.path.splitext(os.path.basename(video_path))[0]
ext = os.path.splitext(video_path)[1].lower()
text_out = os.path.join(OUT_DIR, f"{base_name}_{timestamp}.txt")

audio_extensions = [".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"]

# Extraer audio con ffmpeg si es video
if ext in audio_extensions:
    audio_out = video_path
else:
    audio_out = os.path.join(OUT_DIR, f"{base_name}_{timestamp}.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", audio_out],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"Error extrayendo audio con ffmpeg: {e}")
        sys.exit(1)

client = Together(api_key=api_key)

# Transcribir audio y guardar texto
try:
    with open(audio_out, "rb") as f:
        res = client.audio.transcriptions.create(
            model="openai/whisper-large-v3",
            file=f,
            language="es"
        )
    
    with open(text_out, "w", encoding="utf-8") as f:
        f.write(res.text)
    
    print(f"[+] Transcripción completada: {text_out}")
except Exception as e:
    print(f"Error durante la transcripción: {e}")
