# Arquitectura del Sistema: Transcribe (Herramienta Ligera) 

## 1. Visión General de la Arquitectura (C4 Model)

```mermaid
C4Container
    title Diagrama de Contenedores - Sistema de Transcripción

    Person(user, "Usuario", "Interactúa con el sistema para transcribir archivos de audio y video de forma rápida.")
  
    System_Ext(togetherApi, "Together AI API", "Provee el modelo Whisper (large-v3) para transcripción rápida basada en la nube.")
    System_Ext(ffmpeg, "FFmpeg", "Herramienta del SO para extraer y convertir formatos multimedia.")

    Container_Boundary(c1, "Sistema Transcribe") {
        Container(webApp, "Aplicación Web (Streamlit)", "Python, Streamlit", "Interfaz gráfica simple para cargar archivos y ver/descargar resultados de texto.")
        Container(cliScripts, "Scripts CLI", "Python", "Scripts de consola para automatización por línea de comandos (transcribe_audio.py, etc).")
        Container(storage, "Almacenamiento Local Simple", "File System", "Almacena temporalmente archivos subidos (`/in`) y audios/resultados (`/out`).")
    }

    Rel(user, webApp, "Sube archivos multimedia", "Navegador Web")
    Rel(user, cliScripts, "Ejecuta comandos", "Terminal")
  
    Rel(webApp, storage, "Guarda archivos subidos temporalmente", "I/O")
    Rel(cliScripts, storage, "Lee y escribe archivos", "I/O")

    Rel(webApp, ffmpeg, "Ejecuta para estandarizar audio a WAV 16kHz", "Subprocess")
    Rel(cliScripts, ffmpeg, "Ejecuta para extraer audio", "Subprocess")

    Rel(webApp, togetherApi, "Envía buffer de audio y recibe transcripción", "HTTPS (API REST)")
    Rel(cliScripts, togetherApi, "Envía audio y recibe texto", "HTTPS (API REST)")
```

## 2. Flujo de Ejecución Detallado (Diagrama de Secuencia)

El siguiente diagrama detalla la lógica condicional empleada por Streamlit y el pre-procesamiento del archivo subido.

```mermaid
sequenceDiagram
    actor Usuario
    participant UI as Streamlit UI (app.py)
    participant FS as File System (/in, /out)
    participant FFmpeg as FFmpeg (OS)
    participant API as Together AI API

    Usuario->>UI: Sube un archivo (mp4, mov, mp3, etc.)
    UI->>FS: Guarda el archivo temporalmente en `/in` con Timestamp
  
    alt Es archivo de Audio nativo (.mp3, .wav, etc)
        UI->>UI: Define la ruta del audio original para la API
    else Es Video o formato a procesar (.mp4, .mov, etc)
        UI->>FFmpeg: subprocess.run(ffmpeg -i input -vn -ac 1 -ar 16000 output.wav)
        FFmpeg->>FS: Escribe archivo `output.wav` temporal en `/out`
        UI->>UI: Define la ruta del nuevo `.wav` para la API
    end

    UI->>API: HTTP POST (modelo: whisper-large-v3, archivo: audio)
    API-->>UI: Retorna JSON con la transcripción completa
  
    UI->>FS: Guarda el resultado de texto (`.txt`) en `/out`
    UI->>UI: Actualiza `session_state` con el texto
    UI-->>Usuario: Muestra texto en pantalla y habilita botón de Descarga
```

## 3. Gestión de Estado en Streamlit

Debido a que Streamlit recarga el script en cada interacción del usuario (como al hacer click en un botón de descarga), se utiliza un mecanismo de `session_state` para evitar volver a transcribir el mismo archivo accidentalmente.

- **`st.session_state["file_id"]`**: Se genera un identificador único basado en el nombre y tamaño del archivo (`f"{uploaded_file.name}_{uploaded_file.size}"`).
- **Verificación**: Si el `file_id` actual coincide con el de `session_state`, Streamlit no ejecuta de nuevo el proceso de FFmpeg ni la petición a la API. Directamente renderiza el texto almacenado en memoria.
- **`st.session_state["transcript_text"]`** y **`st.session_state["out_filename"]`**: Almacenan el resultado final y el nombre del archivo para renderizar de manera segura el botón `st.download_button`.

## 4. Componentes Clave

* **`src/app.py`**: El punto de entrada principal para la interfaz gráfica.
* **`key.env`**: Archivo de entorno local (no subido a git) para inyectar `TOGETHER_API_KEY`.
* **Requerimientos del Sistema**: Requiere `ffmpeg` instalado globalmente en la máquina del usuario o servidor que ejecute Streamlit. No requiere base de datos alguna.
