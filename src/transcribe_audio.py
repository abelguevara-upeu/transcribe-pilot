import os
import sys
from datetime import datetime
from together import Together

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_DIR = os.path.join(BASE_DIR, "in")
OUT_DIR = os.path.join(BASE_DIR, "out")
KEY_PATH = os.path.join(BASE_DIR, "key.env")

os.makedirs(IN_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

api_key = open(KEY_PATH).read().strip() if os.path.exists(KEY_PATH) else os.environ.get("TOGETHER_API_KEY")

if not api_key:
    print("Error: API Key de Together no encontrada.")
    sys.exit(1)

if len(sys.argv) > 1:
    raw_input = sys.argv[1]
    audio_path = raw_input if os.path.isabs(raw_input) or os.path.dirname(raw_input) else os.path.join(IN_DIR, raw_input)
else:
    audio_extensions = [".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"]
    files = [f for f in os.listdir(IN_DIR) if os.path.splitext(f)[1].lower() in audio_extensions]
    if files:
        files.sort(key=lambda x: os.path.getmtime(os.path.join(IN_DIR, x)), reverse=True)
        audio_path = os.path.join(IN_DIR, files[0])
    else:
        print("Uso: python src/transcribe_audio.py <archivo_de_audio>")
        sys.exit(1)

if not os.path.exists(audio_path):
    print(f"Error: No se encontró '{audio_path}'")
    sys.exit(1)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
base_name = os.path.splitext(os.path.basename(audio_path))[0]
text_out = os.path.join(OUT_DIR, f"{base_name}_{timestamp}.txt")

client = Together(api_key=api_key)
print(f"Transcribiendo '{audio_path}'...")

try:
    with open(audio_path, "rb") as f:
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
