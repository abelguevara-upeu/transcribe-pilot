import os
from together import Together

# Leer la API key desde el archivo key.env
key_path = "key.env"
if os.path.exists(key_path):
    with open(key_path, "r") as f:
        api_key = f.read().strip()
else:
    api_key = os.environ.get("TOGETHER_API_KEY")

if not api_key:
    print("Error: No se encontró la API key de Together.")
    exit(1)

# Inicializar el cliente
client = Together(api_key=api_key)

# Archivo local que deseas transcribir
audio_file = "videoplayback.mp3"

print(f"Enviando '{audio_file}' a Together AI para transcripción...")

try:
    with open(audio_file, "rb") as file_obj:
        response = client.audio.transcriptions.create(
            model="openai/whisper-large-v3",
            file=file_obj, 
            language="es"
        )
    
    print("\n--- Resultado de la transcripción ---")
    print(response.text)
    
    # Guardar en un archivo de texto
    with open("transcripcion.txt", "w", encoding="utf-8") as out_file:
        out_file.write(response.text)
    print("\n[+] La transcripción se ha guardado en 'transcripcion.txt'")

except Exception as e:
    print(f"Ocurrió un error al transcribir: {e}")
