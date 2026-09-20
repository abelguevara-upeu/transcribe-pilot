import os
import subprocess
from datetime import datetime
import streamlit as st
from together import Together

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_DIR = os.path.join(BASE_DIR, "in")
OUT_DIR = os.path.join(BASE_DIR, "out")
KEY_PATH = os.path.join(BASE_DIR, "key.env")

os.makedirs(IN_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

st.set_page_config(page_title="Transcriptor de Video", page_icon="🎙️")
st.title("🎙️ Transcriptor de Video a Texto")
st.write("Sube tu archivo de video o audio para obtener la transcripción automáticamente.")

api_key = open(KEY_PATH).read().strip() if os.path.exists(KEY_PATH) else os.environ.get("TOGETHER_API_KEY")

if not api_key:
    st.error("Error: API Key de Together no encontrada ('key.env' o TOGETHER_API_KEY).")
    st.stop()

uploaded_file = st.file_uploader("Sube un archivo (mp4, mov, mp3, wav, webm, etc.)", type=["mp4", "mov", "avi", "mkv", "webm", "mp3", "wav", "flac", "m4a", "ogg", "aac", "weba"])

if uploaded_file is not None:
    file_id = f"{uploaded_file.name}_{uploaded_file.size}"
    
    if st.session_state.get("file_id") != file_id:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = os.path.splitext(uploaded_file.name)[0]
        ext = os.path.splitext(uploaded_file.name)[1].lower()
        
        input_path = os.path.join(IN_DIR, f"{base_name}_{timestamp}{ext}")
        text_out = os.path.join(OUT_DIR, f"{base_name}_{timestamp}.txt")
        
        with open(input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
            
        audio_extensions = [".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"]
        
        if ext in audio_extensions:
            audio_out = input_path
        else:
            audio_out = os.path.join(OUT_DIR, f"{base_name}_{timestamp}.wav")
            with st.spinner("Extrayendo audio (WAV 16kHz Mono)..."):
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", input_path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", audio_out],
                        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                except Exception as e:
                    st.error(f"Error en ffmpeg: {e}")
                    st.stop()
                
        with st.spinner("Transcribiendo con Together AI..."):
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

                st.session_state["file_id"] = file_id
                st.session_state["transcript_text"] = res.text
                st.session_state["out_filename"] = f"{base_name}_{timestamp}.txt"
                st.success("¡Transcripción completada!")

            except Exception as e:
                st.error(f"Error durante la transcripción: {e}")
                st.stop()

    if st.session_state.get("file_id") == file_id:
        st.text_area("Resultado:", st.session_state["transcript_text"], height=300)
        st.download_button(
            label="Descargar Texto (.txt)",
            data=st.session_state["transcript_text"],
            file_name=st.session_state["out_filename"],
            mime="text/plain"
        )
