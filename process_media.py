import os
import subprocess
from datetime import datetime
from together import Together

import sys

if len(sys.argv) < 2:
    print("Uso: python process_media.py <nombre_del_video>")
    exit(1)

video_path = os.path.join("in", sys.argv[1])

if not os.path.exists(video_path):
    print(f"Error: No se encontró '{video_path}'")
    exit(1)

key_path = "key.env"
api_key = open(key_path).read().strip() if os.path.exists(key_path) else os.environ.get("TOGETHER_API_KEY")

if not api_key:
    print("Error: API Key no encontrada.")
    exit(1)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
base_name = os.path.splitext(sys.argv[1])[0]
audio_out = os.path.join("out", f"{base_name}_{timestamp}.mp3")
text_out = os.path.join("out", f"{base_name}_{timestamp}.txt")

try:
    subprocess.run(
        ["ffmpeg", "-i", video_path, "-vn", "-acodec", "libmp3lame", audio_out],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
except Exception as e:
    print(f"Error extrayendo audio: {e}")
    exit(1)

client = Together(api_key=api_key)

try:
    with open(audio_out, "rb") as f:
        res = client.audio.transcriptions.create(
            model="openai/whisper-large-v3",
            file=f,
            language="es"
        )
    
    with open(text_out, "w", encoding="utf-8") as f:
        f.write(res.text)
    
    print(text_out)
except Exception as e:
    print(f"Error transcribiendo: {e}")
