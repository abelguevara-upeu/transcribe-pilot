import streamlit as st
import os
import subprocess
from datetime import datetime
from together import Together

st.set_page_config(page_title="Transcriptor de Video", page_icon="🎙️")

st.title("🎙️ Transcriptor de Video a Texto")
st.write("Sube tu archivo de video o audio para obtener la transcripción automáticamente.")

# Asegurarse de que existan las carpetas de trabajo
os.makedirs("in", exist_ok=True)
os.makedirs("out", exist_ok=True)

# 1. Cargar API Key
key_path = "key.env"
api_key = open(key_path).read().strip() if os.path.exists(key_path) else os.environ.get("TOGETHER_API_KEY")

if not api_key:
    st.error("Error: API Key de Together no encontrada. Asegúrate de tener el archivo 'key.env'.")
    st.stop()

# 2. Interfaz de subida de archivo
uploaded_file = st.file_uploader("Sube un archivo (mp4, mov, mp3, wav, etc.)", type=["mp4", "mov", "avi", "mkv", "mp3", "wav", "flac", "m4a"])

if uploaded_file is not None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.splitext(uploaded_file.name)[0]
    ext = os.path.splitext(uploaded_file.name)[1]
    
    input_path = os.path.join("in", f"{base_name}_{timestamp}{ext}")
    audio_out = os.path.join("out", f"{base_name}_{timestamp}.mp3")
    text_out = os.path.join("out", f"{base_name}_{timestamp}.txt")
    
    # Guardar archivo en la carpeta in/
    with open(input_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
        
    st.info("Archivo cargado con éxito. Iniciando proceso...")
    
    # 3. Extraer audio
    with st.spinner("Preparando/Extrayendo audio (ffmpeg)..."):
        try:
            # ffmpeg maneja bien cualquier archivo de medios. Si es video saca audio, si es audio lo convierte/copia.
            subprocess.run(
                ["ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "libmp3lame", audio_out],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            st.error(f"Ocurrió un error al procesar con ffmpeg: {e}")
            st.stop()
            
    # 4. Transcripción
    with st.spinner("Transcribiendo con Together AI... Esto tomará un momento."):
        client = Together(api_key=api_key)
        try:
            with open(audio_out, "rb") as f:
                res = client.audio.transcriptions.create(
                    model="openai/whisper-large-v3",
                    file=f,
                    language="es"
                )
            
            # Guardar el texto en un archivo txt
            with open(text_out, "w", encoding="utf-8") as f:
                f.write(res.text)
                
            st.success("¡Transcripción completada exitosamente!")
            
            # Mostrar resultado y botón de descarga
            st.text_area("Resultado de la Transcripción:", res.text, height=300)
            st.download_button(
                label="Descargar Texto (.txt)",
                data=res.text,
                file_name=f"{base_name}_{timestamp}.txt",
                mime="text/plain"
            )
            
        except Exception as e:
            st.error(f"Error durante la transcripción: {e}")
