#!/usr/bin/env python3
"""Interfaz nativa (Qt). Historial de transcripciones.

La logica vive en core.py y db.py, compartida con el CLI.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt, QUrl, QThread, Signal
from PySide6.QtGui import (
    QAction, QDesktopServices, QKeySequence, QPen, QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QVBoxLayout, QWidget, QComboBox, QInputDialog, QSlider, QStyle,
    QStyledItemDelegate,
)

import core
import db


class RedoWorker(QThread):
    """La llamada a la API bloquea: va en su propio hilo."""
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, row, language):
        super().__init__()
        self.row, self.language = row, language

    def run(self):
        try:
            from together import Together
            client = Together(api_key=core.get_api_key())
            self.done.emit(core.retranscribe_row(self.row, client, self.language))
        except Exception as e:
            self.failed.emit(str(e))


def nombre_visible(row):
    """Solo el titulo: tipo, fecha y formato ya van en el subtitulo.

    De "video_20260915-1104_whatsapp-video_mp4.txt" deja "whatsapp-video".
    """
    base = os.path.splitext(row["filename"])[0]
    partes = base.split("_")
    # <tipo>_<fecha>_<slug>. El slug puede llevar guiones bajos, asi que se
    # descartan solo las dos primeras partes.
    if len(partes) >= 3 and partes[0] in ("audio", "video"):
        return "_".join(partes[2:]) or base
    return base


def subtitulo(row):
    """Fecha, tipo, idioma, formato y tamaño, en una linea."""
    fecha = row["created_at"][:16].replace("T", " ")
    partes = [fecha]
    ext = (row["ext"] or "").lower()
    if ext:
        partes.append("vídeo" if ext in core.VIDEO_EXTS else "audio")
    if row["language"]:
        partes.append(row["language"])
    if row["ext"]:
        partes.append(row["ext"].lstrip(".").upper())
    tam = row["size_bytes"]
    if tam:
        # Un archivo de 400 KB no deberia mostrarse como "0 MB".
        partes.append(f"{tam / 1_000_000:.0f} MB" if tam >= 1_000_000
                      else f"{tam / 1000:.0f} KB")
    return "  ·  ".join(partes)


class FilaDelegate(QStyledItemDelegate):
    """Pinta cada entrada en dos lineas con un separador inferior.

    Sin esto las entradas se confunden entre si: no se ve donde acaba una.
    """
    ALTO = 58

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(self.ALTO)
        return size

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        seleccionado = option.state & QStyle.State_Selected

        if seleccionado:
            painter.fillRect(rect, option.palette.highlight())
            color_txt = option.palette.highlightedText().color()
            color_sub = color_txt
        else:
            color_txt = option.palette.text().color()
            color_sub = option.palette.placeholderText().color()

        titulo = index.data(Qt.DisplayRole) or ""
        subtitulo = index.data(Qt.UserRole + 1) or ""

        x = rect.left() + 10
        ancho = rect.width() - 20

        f = painter.font()
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(color_txt)
        metrics = painter.fontMetrics()
        painter.drawText(
            x, rect.top() + 8, ancho, metrics.height(),
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(titulo, Qt.ElideMiddle, ancho),
        )

        f.setBold(False)
        f.setPointSizeF(max(9.0, f.pointSizeF() - 1))
        painter.setFont(f)
        painter.setPen(color_sub)
        m2 = painter.fontMetrics()
        painter.drawText(
            x, rect.top() + 30, ancho, m2.height(),
            Qt.AlignLeft | Qt.AlignVCenter,
            m2.elidedText(subtitulo, Qt.ElideRight, ancho),
        )

        if not seleccionado:
            painter.setPen(QPen(option.palette.mid().color(), 1))
            painter.drawLine(rect.left() + 8, rect.bottom(),
                             rect.right() - 8, rect.bottom())
        painter.restore()


class TranscribeWorker(QThread):
    """Transcribe una lista de archivos, uno a uno, fuera del hilo de UI."""
    progress = Signal(int, int, str)   # indice, total, nombre
    item_done = Signal(dict)
    finished_all = Signal(list)

    def __init__(self, paths, language, convertir=None):
        super().__init__()
        self.paths, self.language = paths, language
        # (altura, bitrate) para recodificar antes de transcribir, o None
        self.convertir = convertir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    # Varias transcripciones a la vez: cada llamada a la API tarda minutos y
    # el tiempo es de espera de red, no de CPU. Un tope bajo evita saturar
    # la cuenta y mantiene el orden de los resultados comprensible.
    PARALELAS = 3

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from together import Together

        try:
            client = Together(api_key=core.get_api_key())
        except Exception as e:
            self.finished_all.emit([{"ok": False, "error": str(e)}])
            return

        total = len(self.paths)
        resultados = []
        hechos = 0

        def procesar(path):
            origen = path
            if self.convertir and core.necesita_conversion(path):
                altura, bitrate = self.convertir
                # El original se conserva; se transcribe el convertido,
                # que es el que quedara referenciado en la BD.
                path = core.convertir_a_mp4(path, altura=altura,
                                            bitrate=bitrate)
            row_id, text, reused = core.transcribe_file(
                path, client, language=self.language,
                original_name=os.path.basename(origen),
            )
            return {"ok": True, "file": origen, "id": row_id,
                    "reused": reused, "chars": len(text)}

        with ThreadPoolExecutor(max_workers=self.PARALELAS) as pool:
            pendientes = {}
            for path in self.paths:
                if self._cancel:
                    break
                pendientes[pool.submit(procesar, path)] = path

            self.progress.emit(0, total, f"{len(pendientes)} en curso")
            for fut in as_completed(pendientes):
                path = pendientes[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    r = {"ok": False, "file": path, "error": str(e)}
                resultados.append(r)
                hechos += 1
                self.progress.emit(hechos, total, os.path.basename(path))
                self.item_done.emit(r)

        self.finished_all.emit(resultados)


class CodecAviso(QDialog):
    """Aviso previo cuando entran videos que el reproductor no decodifica.

    No se rechaza el archivo: su audio se transcribe igual de bien. Solo se
    avisa de que no se vera la imagen, y se ofrece convertir antes.
    """
    CONVERTIR, CONTINUAR, CANCELAR = 0, 1, 2

    def __init__(self, problemáticos, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Formato de vídeo no reproducible")
        self.setMinimumWidth(520)
        self.resultado = self.CANCELAR

        v = QVBoxLayout(self)
        n = len(problemáticos)
        v.addWidget(QLabel(
            f"<b>{n} archivo(s) usan un códec que este reproductor no "
            "muestra:</b>"
        ))
        detalle = "<ul>" + "".join(
            f"<li>{os.path.basename(p)} — {c.upper()}</li>"
            for p, c in problemáticos[:6]
        ) + ("<li>…</li>" if n > 6 else "") + "</ul>"
        v.addWidget(QLabel(detalle))
        v.addWidget(QLabel(
            "El audio se transcribe igual. La diferencia es solo si podrás "
            "ver la imagen dentro de la aplicación."
        ))

        v.addWidget(QLabel("Calidad de la conversión:"))
        self.combo = QComboBox()
        self.combo.addItems(list(ConvertDialog.PERFILES))
        v.addWidget(self.combo)

        botones = QHBoxLayout()
        b_conv = QPushButton("Convertir y transcribir")
        b_conv.setDefault(True)
        b_conv.clicked.connect(lambda: self._elegir(self.CONVERTIR))
        botones.addWidget(b_conv)

        b_seguir = QPushButton("Solo transcribir")
        b_seguir.clicked.connect(lambda: self._elegir(self.CONTINUAR))
        botones.addWidget(b_seguir)

        b_cancel = QPushButton("Cancelar")
        b_cancel.clicked.connect(lambda: self._elegir(self.CANCELAR))
        botones.addWidget(b_cancel)
        v.addLayout(botones)

    def _elegir(self, valor):
        self.resultado = valor
        self.accept() if valor != self.CANCELAR else self.reject()

    def perfil(self):
        return ConvertDialog.PERFILES[self.combo.currentText()]


class AudioPrepWorker(QThread):
    """Extrae el audio estereo de un video no reproducible, sin bloquear."""
    listo = Signal(int, str)

    def __init__(self, row):
        super().__init__()
        self.row = dict(row)

    def run(self):
        try:
            path = core.audio_reproducible(self.row)
            if path:
                self.listo.emit(self.row["id"], path)
        except Exception:
            pass


class ConvertWorker(QThread):
    """ffmpeg bloquea varios minutos: fuera del hilo de UI."""
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, src, altura, bitrate, reemplazar):
        super().__init__()
        self.src, self.altura = src, altura
        self.bitrate, self.reemplazar = bitrate, reemplazar

    def run(self):
        try:
            dest = core.convertir_a_mp4(self.src, altura=self.altura,
                                        bitrate=self.bitrate)
            if self.reemplazar:
                os.remove(self.src)
            self.done.emit(dest)
        except Exception as e:
            self.failed.emit(str(e))


class ConvertDialog(QDialog):
    """Opciones de conversion, con el coste en disco a la vista."""

    # None = sin reescalar. El resto fija la altura; el ancho se ajusta solo
    # para no deformar, asi que funciona igual en vertical y horizontal.
    PERFILES = {
        "Resolución original": (None, "1500k"),
        "1080p": (1080, "1500k"),
        "720p": (720, "1200k"),
        "480p": (480, "700k"),
    }

    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.row = row
        self.setWindowTitle("Convertir a MP4")
        self.setMinimumWidth(460)

        src = row["input_path"]
        tam = os.path.getsize(src) / 1_000_000
        codec = core.video_codec(src) or "?"
        ancho, alto = core.video_resolucion(src)
        res = f"{ancho}×{alto}" if ancho else "resolución desconocida"

        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"<b>{os.path.basename(src)}</b>"))
        v.addWidget(QLabel(
            f"Códec actual: {codec.upper()} · {res} · {tam:.0f} MB<br>"
            "Se creará una copia en H.264, reproducible en cualquier lugar."
        ))

        v.addWidget(QLabel("Calidad:"))
        self.combo = QComboBox()
        self.combo.addItems(list(self.PERFILES))
        v.addWidget(self.combo)

        self.chk = QCheckBox("Eliminar el archivo original al terminar")
        self.chk.setToolTip(
            "La conversión tiene pérdida: el original no se puede recuperar."
        )
        v.addWidget(self.chk)

        self.aviso = QLabel()
        self.aviso.setWordWrap(True)
        self.aviso.setStyleSheet("color: #b45309;")
        v.addWidget(self.aviso)
        self.alto_original = alto
        self.chk.stateChanged.connect(self.refresh)
        self.combo.currentTextChanged.connect(self.refresh)
        self.refresh()

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Convertir")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def refresh(self):
        lineas = []
        altura, _ = self.PERFILES[self.combo.currentText()]
        if altura and self.alto_original and altura > self.alto_original:
            lineas.append(
                f"El vídeo es de {self.alto_original}p: escalar a {altura}p "
                "no añade detalle y agranda el archivo."
            )
        if self.chk.isChecked():
            lineas.append("El original se borrará. La conversión pierde "
                          "calidad y no es reversible.")
        else:
            lineas.append("Se conservarán ambos archivos, ocupando el doble "
                          "de espacio.")
        self.aviso.setText(" ".join(lineas))

    def opciones(self):
        altura, bitrate = self.PERFILES[self.combo.currentText()]
        return altura, bitrate, self.chk.isChecked()


class VersionesDialog(QDialog):
    """Versiones anteriores del texto, con vista previa antes de restaurar."""

    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.row = row
        self.setWindowTitle("Versiones anteriores")
        self.resize(640, 460)
        self.restaurar_id = None

        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"<b>{nombre_visible(row)}</b>"))

        self.lista = QListWidget()
        actual = QListWidgetItem(
            f"Actual · {row['created_at'][:16]} · "
            f"{row['language'] or '?'} · {len(row['text'] or '')} caracteres"
        )
        actual.setData(Qt.UserRole, None)
        self.lista.addItem(actual)

        for ver in db.versions(row["id"]):
            it = QListWidgetItem(
                f"{ver['created_at'][:16]} · {ver['language'] or '?'} · "
                f"{len(ver['text'])} caracteres "
                f"(reemplazada el {ver['replaced_at'][:16]})"
            )
            it.setData(Qt.UserRole, ver["id"])
            self.lista.addItem(it)

        self.lista.currentItemChanged.connect(self.vista_previa)
        v.addWidget(self.lista)

        self.texto = QPlainTextEdit(readOnly=True)
        v.addWidget(self.texto, 1)

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Ok).setText("Restaurar")
        self.bb.accepted.connect(self.aceptar)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)

        self.lista.setCurrentRow(0)

    def vista_previa(self):
        it = self.lista.currentItem()
        if not it:
            return
        vid = it.data(Qt.UserRole)
        if vid is None:
            self.texto.setPlainText(self.row["text"] or "")
        else:
            ver = next((x for x in db.versions(self.row["id"])
                        if x["id"] == vid), None)
            self.texto.setPlainText(ver["text"] if ver else "")
        # Restaurar la version que ya esta activa no tiene sentido.
        self.bb.button(QDialogButtonBox.Ok).setEnabled(vid is not None)

    def aceptar(self):
        it = self.lista.currentItem()
        self.restaurar_id = it.data(Qt.UserRole) if it else None
        self.accept()


class DeleteDialog(QDialog):
    """Confirmacion que lista los archivos reales que se van a borrar."""

    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.row = row
        self.setWindowTitle("Eliminar transcripción")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{nombre_visible(row)}</b>"))

        original = row["input_path"]
        self.has_original = bool(original) and os.path.exists(original)
        self.chk = QCheckBox(
            f"Eliminar también el original ({os.path.basename(original)})"
            if self.has_original else "No hay archivo original en in/"
        )
        self.chk.setEnabled(self.has_original)
        self.chk.stateChanged.connect(self.refresh)
        layout.addWidget(self.chk)

        self.lista = QLabel()
        self.lista.setWordWrap(True)
        self.lista.setStyleSheet("color: #b45309; padding: 6px;")
        layout.addWidget(self.lista)

        botones = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        botones.button(QDialogButtonBox.Ok).setText("Eliminar")
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        layout.addWidget(botones)
        self.refresh()

    def refresh(self):
        archivos = db.files_for(self.row, include_original=self.chk.isChecked())
        if archivos:
            items = "".join(f"<li>{os.path.basename(p)}</li>" for p in archivos)
            self.lista.setText(
                f"Se eliminará la entrada del historial y:<ul>{items}</ul>"
            )
        else:
            self.lista.setText(
                "Se eliminará la entrada. No queda ningún archivo suyo en disco."
            )

    def remove_original(self):
        return self.chk.isChecked()


class Historial(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transcripciones")
        self.resize(1000, 700)
        self.worker = None
        self.lote = None
        self.prep = None
        self.setAcceptDrops(True)

        raiz = QVBoxLayout(self)

        # --- barra de transcripcion ---
        acciones = QHBoxLayout()
        self.btn_add = QPushButton("Añadir archivos…")
        self.btn_add.clicked.connect(self.elegir_archivos)
        acciones.addWidget(self.btn_add)

        acciones.addWidget(QLabel("Idioma:"))
        self.combo_idioma = QComboBox()
        self.combo_idioma.addItems(list(core.IDIOMAS))
        self.combo_idioma.setCurrentText("Español")
        acciones.addWidget(self.combo_idioma)

        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.clicked.connect(self.cancelar_lote)
        self.btn_cancelar.hide()
        acciones.addWidget(self.btn_cancelar)

        acciones.addStretch(1)
        raiz.addLayout(acciones)

        self.barra_progreso = QProgressBar()
        self.barra_progreso.hide()
        raiz.addWidget(self.barra_progreso)

        self.estado = QLabel()
        self.estado.setStyleSheet("color: gray;")
        self.estado.hide()
        raiz.addWidget(self.estado)

        # --- busqueda ---
        barra = QHBoxLayout()
        self.buscar = QLineEdit(placeholderText="Buscar por nombre o contenido…")
        self.buscar.textChanged.connect(self.cargar)
        barra.addWidget(self.buscar)
        self.contador = QLabel()
        barra.addWidget(self.contador)
        raiz.addLayout(barra)

        split = QSplitter(Qt.Horizontal)

        self.lista = QListWidget()
        self.lista.setItemDelegate(FilaDelegate(self.lista))
        self.lista.setSpacing(0)
        self.lista.setUniformItemSizes(True)
        self.lista.setAlternatingRowColors(False)
        self.lista.currentItemChanged.connect(self.mostrar)
        self.lista.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lista.customContextMenuRequested.connect(self.menu_contextual)
        split.addWidget(self.lista)

        panel = QWidget()
        pv = QVBoxLayout(panel)

        cab = QHBoxLayout()
        self.titulo = QLabel()
        self.titulo.setStyleSheet("font-weight: 600;")
        cab.addWidget(self.titulo, 1)
        self.btn_menu = QPushButton("⋮")
        self.btn_menu.setFixedWidth(32)
        self.btn_menu.clicked.connect(self.menu_boton)
        cab.addWidget(self.btn_menu)
        pv.addLayout(cab)

        self.video = QVideoWidget()
        self.video.setMinimumHeight(220)
        self.video.hide()
        pv.addWidget(self.video)

        self.player = QMediaPlayer()
        self.salida = QAudioOutput()
        self.player.setAudioOutput(self.salida)
        self.player.errorOccurred.connect(self.error_medio)
        self.player.positionChanged.connect(self.actualizar_posicion)
        self.player.durationChanged.connect(self.actualizar_duracion)
        self.player.mediaStatusChanged.connect(self.estado_medio)
        self.player.playbackStateChanged.connect(self.estado_reproduccion)

        controles = QHBoxLayout()
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedWidth(40)
        self.btn_play.clicked.connect(self.toggle_play)
        controles.addWidget(self.btn_play)

        self.t_actual = QLabel("0:00")
        self.t_actual.setStyleSheet("color: gray;")
        controles.addWidget(self.t_actual)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        # sliderMoved solo se emite al arrastrar: evita el bucle con
        # positionChanged, que mueve el slider mientras reproduce.
        self.slider.sliderMoved.connect(self.player_seek)
        self.slider.sliderReleased.connect(
            lambda: self.player_seek(self.slider.value())
        )
        controles.addWidget(self.slider, 1)

        self.t_total = QLabel("0:00")
        self.t_total.setStyleSheet("color: gray;")
        controles.addWidget(self.t_total)

        self.velocidad = QComboBox()
        self.velocidad.addItems(["0.75×", "1×", "1.25×", "1.5×", "2×"])
        self.velocidad.setCurrentText("1×")
        self.velocidad.currentTextChanged.connect(self.cambiar_velocidad)
        controles.addWidget(self.velocidad)
        pv.addLayout(controles)

        self.medio = QLabel()
        self.medio.setStyleSheet("color: gray;")
        pv.addWidget(self.medio)

        self.texto = QPlainTextEdit(readOnly=True)
        pv.addWidget(self.texto, 1)

        split.addWidget(panel)
        split.setSizes([340, 660])
        raiz.addWidget(split, 1)

        QShortcut(QKeySequence.Delete, self.lista, self.eliminar)
        QShortcut(QKeySequence(Qt.Key_F2), self.lista, self.renombrar)
        # Atajos de reproduccion, al estilo de un reproductor cualquiera.
        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.saltar(-5000))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.saltar(5000))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.saltar(-30000))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.saltar(30000))
        self.cargar()

    # --- transcripcion ---
    def elegir_archivos(self):
        filtros = " ".join(f"*{e}" for e in core.UPLOAD_EXTS)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Seleccionar audio o video", os.path.expanduser("~"),
            f"Audio y video ({filtros});;Todos los archivos (*)",
        )
        if paths:
            self.transcribir(paths)

    def transcribir(self, paths):
        validos = [p for p in paths
                   if os.path.splitext(p)[1].lower() in core.UPLOAD_EXTS]
        if not validos:
            QMessageBox.warning(self, "Formato no admitido",
                                "Ninguno de los archivos tiene un formato admitido.")
            return

        # Detectar codecs no reproducibles antes de empezar, para preguntar
        # una sola vez en lugar de archivo por archivo.
        problematicos = [(p, core.video_codec(p) or "?")
                         for p in validos if core.necesita_conversion(p)]
        perfil = None
        if problematicos:
            aviso = CodecAviso(problematicos, self)
            aviso.exec()
            if aviso.resultado == CodecAviso.CANCELAR:
                return
            if aviso.resultado == CodecAviso.CONVERTIR:
                perfil = aviso.perfil()

        self.btn_add.setEnabled(False)
        self.combo_idioma.setEnabled(False)
        self.btn_cancelar.show()
        self.barra_progreso.setRange(0, len(validos))
        self.barra_progreso.setValue(0)
        self.barra_progreso.show()
        self.estado.show()

        self.lote = TranscribeWorker(
            validos, core.IDIOMAS[self.combo_idioma.currentText()], perfil
        )
        self.lote.progress.connect(self.lote_progreso)
        # Durante el lote solo se refresca la lista, sin tocar la seleccion
        # ni el reproductor: recargar entero reabria archivos en el hilo de
        # UI y congelaba la ventana.
        self.lote.item_done.connect(lambda _r: self.cargar(preservar=True))
        self.lote.finished_all.connect(self.lote_fin)
        self.lote.start()

    def lote_progreso(self, hechos, total, nombre):
        self.barra_progreso.setValue(hechos)
        if hechos == 0:
            self.estado.setText(f"Transcribiendo {total} archivo(s) — {nombre}…")
        else:
            self.estado.setText(
                f"Completados {hechos}/{total} — último: {nombre}"
            )

    def cancelar_lote(self):
        if getattr(self, "lote", None):
            self.lote.cancel()
            self.estado.setText("Cancelando tras el archivo en curso…")

    def lote_fin(self, resultados):
        self.btn_add.setEnabled(True)
        self.combo_idioma.setEnabled(True)
        self.btn_cancelar.hide()
        self.barra_progreso.hide()

        ok = [r for r in resultados if r.get("ok")]
        reusados = [r for r in ok if r.get("reused")]
        fallos = [r for r in resultados if not r.get("ok")]

        partes = [f"{len(ok)} transcrito(s)"]
        if reusados:
            partes.append(f"{len(reusados)} ya existía(n)")
        if fallos:
            partes.append(f"{len(fallos)} con error")
        self.estado.setText(" · ".join(partes))

        if fallos:
            detalle = "\n".join(
                f"• {os.path.basename(f.get('file', '?'))}: {f['error']}"
                for f in fallos
            )
            QMessageBox.warning(self, "Errores durante la transcripción", detalle)
        self.cargar()

    # --- arrastrar y soltar ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if paths:
            self.transcribir(paths)

    # --- datos ---
    def cargar(self, preservar=False):
        actual = self.fila_actual()
        if preservar:
            # Evita que reconstruir la lista dispare mostrar() y con ello
            # una recarga del reproductor.
            self.lista.blockSignals(True)
        self.lista.clear()
        self.filas = db.list_all(self.buscar.text())
        for r in self.filas:
            item = QListWidgetItem(nombre_visible(r))
            item.setData(Qt.UserRole, r["id"])
            item.setData(Qt.UserRole + 1, subtitulo(r))
            self.lista.addItem(item)
        self.contador.setText(f"{len(self.filas)} transcripción(es)")
        if self.filas:
            idx = next((i for i, r in enumerate(self.filas)
                        if actual and r["id"] == actual["id"]), 0)
            self.lista.setCurrentRow(idx)
        elif not preservar:
            self.limpiar()

        if preservar:
            self.lista.blockSignals(False)

    def fila_actual(self):
        item = self.lista.currentItem()
        if not item:
            return None
        rid = item.data(Qt.UserRole)
        return next((r for r in self.filas if r["id"] == rid), None)

    def limpiar(self):
        self.titulo.clear()
        self.texto.clear()
        self.medio.clear()
        self.video.hide()
        self.player.stop()

    def mostrar(self):
        row = self.fila_actual()
        if not row:
            return self.limpiar()

        self.titulo.setText(nombre_visible(row))
        self.texto.setPlainText(row["text"] or "")

        path, es_video = core.playable_media(row)
        self.player.stop()
        self.btn_play.setText("▶")
        self.slider.setRange(0, 0)
        self.t_actual.setText("0:00")
        self.t_total.setText("0:00")

        if not path:
            self.video.hide()
            self.medio.setText("Archivo de origen no disponible en disco.")
            self.btn_play.setEnabled(False)
            self.slider.setEnabled(False)
            self.player.setSource(QUrl())
            return
        self.slider.setEnabled(True)

        # El destino de video debe existir y estar visible ANTES de fijar la
        # fuente: si no, el reproductor arranca sin superficie donde pintar y
        # se queda cargando sin mostrar imagen.
        self.video.setVisible(es_video)
        self.player.setVideoOutput(self.video if es_video else None)
        self.player.setSource(QUrl.fromLocalFile(path))

        etiqueta = os.path.basename(path)
        original = row["input_path"]
        if (not es_video and original
                and os.path.splitext(original)[1].lower() in core.VIDEO_EXTS):
            codec = core.video_codec(original)
            etiqueta += (f"  ·  solo audio: {codec.upper()} no se reproduce aquí"
                         if codec else "  ·  solo audio")
            # Si aun no existe el audio estereo, se extrae en segundo plano.
            if not path.endswith("_play.m4a"):
                etiqueta += "  ·  preparando audio…"
                self.prep = AudioPrepWorker(row)
                self.prep.listo.connect(self.audio_listo)
                self.prep.start()
        self.medio.setText(etiqueta)
        self.btn_play.setEnabled(True)

    # --- acciones ---
    def construir_menu(self):
        menu = QMenu(self)
        row = self.fila_actual()
        hay_audio = row and core.playable_media(row)[0]

        a_copiar = QAction("Copiar texto", self)
        a_copiar.triggered.connect(self.copiar)
        menu.addAction(a_copiar)

        a_renombrar = QAction("Renombrar…", self)
        a_renombrar.setEnabled(bool(row))
        a_renombrar.triggered.connect(self.renombrar)
        menu.addAction(a_renombrar)

        menu.addSeparator()
        n_ver = len(db.versions(row["id"])) if row else 0
        a_ver = QAction(
            f"Versiones anteriores ({n_ver})…" if n_ver else "Versiones anteriores",
            self)
        a_ver.setEnabled(n_ver > 0)
        a_ver.triggered.connect(self.ver_versiones)
        menu.addAction(a_ver)

        a_guardar = QAction("Guardar como .txt…", self)
        a_guardar.triggered.connect(self.guardar)
        menu.addAction(a_guardar)

        a_revelar = QAction("Mostrar en Finder", self)
        a_revelar.setEnabled(bool(hay_audio))
        a_revelar.triggered.connect(self.revelar)
        menu.addAction(a_revelar)

        # El reproductor integrado no decodifica VP9/AV1; VLC o el navegador si.
        original = row["input_path"] if row else None
        a_externo = QAction("Abrir con el reproductor del sistema", self)
        a_externo.setEnabled(bool(original) and os.path.exists(original))
        a_externo.triggered.connect(self.abrir_externo)
        menu.addAction(a_externo)

        menu.addSeparator()
        original = row["input_path"] if row else None
        tiene_video = (bool(original) and os.path.exists(original)
                       and os.path.splitext(original)[1].lower() in core.VIDEO_EXTS)
        a_conv = QAction("Convertir a MP4…", self)
        a_conv.setEnabled(tiene_video)
        if tiene_video and core.necesita_conversion(original):
            a_conv.setText("Convertir a MP4 (recomendado)…")
        a_conv.triggered.connect(self.convertir)
        menu.addAction(a_conv)

        a_redo = QAction("Volver a transcribir…", self)
        a_redo.setEnabled(bool(hay_audio))
        a_redo.triggered.connect(self.retranscribir)
        menu.addAction(a_redo)

        menu.addSeparator()
        a_del = QAction("Eliminar…", self)
        a_del.triggered.connect(self.eliminar)
        menu.addAction(a_del)
        return menu

    def menu_boton(self):
        self.construir_menu().exec(
            self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft())
        )

    def menu_contextual(self, punto):
        if self.lista.itemAt(punto):
            self.construir_menu().exec(self.lista.mapToGlobal(punto))

    def audio_listo(self, row_id, path):
        # Solo si la fila sigue seleccionada: el usuario pudo cambiar.
        actual = self.fila_actual()
        if not actual or actual["id"] != row_id:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            return          # no interrumpir una reproduccion en curso
        self.player.setSource(QUrl.fromLocalFile(path))
        self.medio.setText(os.path.basename(path))

    def error_medio(self, _error, cadena):
        self.medio.setText(f"No se pudo reproducir: {cadena}")
        self.btn_play.setEnabled(False)
        self.video.hide()

    def estado_medio(self, estado):
        if estado == QMediaPlayer.LoadingMedia:
            self.medio.setText("Cargando…")
        elif estado == QMediaPlayer.LoadedMedia:
            row = self.fila_actual()
            path, _ = core.playable_media(row) if row else (None, False)
            if path:
                self.medio.setText(os.path.basename(path))
        elif estado == QMediaPlayer.InvalidMedia:
            self.medio.setText("Formato no reproducible por el sistema.")
            self.btn_play.setEnabled(False)
            self.video.hide()
        elif estado == QMediaPlayer.EndOfMedia:
            self.btn_play.setText("▶")

    def estado_reproduccion(self, estado):
        # Mantiene el boton sincronizado aunque la reproduccion pare sola.
        self.btn_play.setText(
            "⏸" if estado == QMediaPlayer.PlayingState else "▶"
        )

    @staticmethod
    def _fmt(ms):
        seg = max(0, ms) // 1000
        h, resto = divmod(seg, 3600)
        m, sg = divmod(resto, 60)
        return f"{h}:{m:02}:{sg:02}" if h else f"{m}:{sg:02}"

    def actualizar_posicion(self, ms):
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.t_actual.setText(self._fmt(ms))

    def actualizar_duracion(self, ms):
        self.slider.setRange(0, ms)
        self.t_total.setText(self._fmt(ms))

    def player_seek(self, ms):
        self.player.setPosition(ms)
        self.t_actual.setText(self._fmt(ms))

    def saltar(self, delta_ms):
        if self.player.duration() > 0:
            self.player_seek(
                min(max(0, self.player.position() + delta_ms),
                    self.player.duration())
            )

    def cambiar_velocidad(self, texto):
        self.player.setPlaybackRate(float(texto.rstrip("×")))

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def copiar(self):
        row = self.fila_actual()
        if row:
            QApplication.clipboard().setText(row["text"] or "")

    def guardar(self):
        row = self.fila_actual()
        if not row:
            return
        destino, _ = QFileDialog.getSaveFileName(
            self, "Guardar transcripción", row["filename"], "Texto (*.txt)"
        )
        if destino:
            with open(destino, "w", encoding="utf-8") as f:
                f.write(row["text"] or "")

    def revelar(self):
        row = self.fila_actual()
        path, _ = core.playable_media(row) if row else (None, False)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def abrir_externo(self):
        row = self.fila_actual()
        path = row["input_path"] if row else None
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def renombrar(self):
        row = self.fila_actual()
        if not row:
            return

        actual = nombre_visible(row)
        titulo, ok = QInputDialog.getText(
            self, "Renombrar transcripción",
            "Título (la fecha y el tipo se conservan):",
            text=actual,
        )
        if not ok or titulo.strip() == actual:
            return

        try:
            _, nuevo = core.renombrar_fila(row, titulo)
        except core.TranscribeError as e:
            QMessageBox.warning(self, "No se pudo renombrar", str(e))
            return
        except OSError as e:
            QMessageBox.critical(self, "Error al renombrar el archivo", str(e))
            return

        self.estado.setText(f"Renombrado: {os.path.splitext(nuevo)[0]}")
        self.estado.show()
        self.cargar()

    def ver_versiones(self):
        row = self.fila_actual()
        if not row:
            return
        dlg = VersionesDialog(row, self)
        if dlg.exec() == QDialog.Accepted and dlg.restaurar_id:
            db.restore_version(dlg.restaurar_id)
            self.estado.setText("Versión restaurada.")
            self.estado.show()
            self.cargar()

    def convertir(self):
        row = self.fila_actual()
        if not row or not row["input_path"]:
            return

        dlg = ConvertDialog(row, self)
        if dlg.exec() != QDialog.Accepted:
            return
        altura, bitrate, reemplazar = dlg.opciones()

        self.player.stop()
        self.btn_menu.setEnabled(False)
        self.barra_progreso.setRange(0, 0)   # indeterminada: ffmpeg no reporta
        self.barra_progreso.show()
        self.estado.setText(f"Convirtiendo {os.path.basename(row['input_path'])}…")
        self.estado.show()

        self.conv_row, self.conv_reemplaza = row, reemplazar
        self.conversor = ConvertWorker(row["input_path"], altura, bitrate,
                                       reemplazar)
        self.conversor.done.connect(self.conversion_ok)
        self.conversor.failed.connect(self.conversion_error)
        self.conversor.start()

    def conversion_ok(self, dest):
        self.btn_menu.setEnabled(True)
        self.barra_progreso.hide()
        self.barra_progreso.setRange(0, 1)

        if self.conv_reemplaza:
            # El original ya no existe: la fila debe apuntar al convertido.
            with db.connect() as conn:
                conn.execute(
                    "UPDATE transcriptions SET input_path = ?, ext = ?,"
                    " size_bytes = ? WHERE id = ?",
                    (dest, ".mp4", os.path.getsize(dest), self.conv_row["id"]),
                )
        tam = os.path.getsize(dest) / 1_000_000
        self.estado.setText(f"Convertido: {os.path.basename(dest)} ({tam:.0f} MB)")
        self.cargar()

    def conversion_error(self, msg):
        self.btn_menu.setEnabled(True)
        self.barra_progreso.hide()
        self.barra_progreso.setRange(0, 1)
        self.estado.setText("Conversión fallida.")
        QMessageBox.critical(self, "Error al convertir", msg)

    def retranscribir(self):
        row = self.fila_actual()
        if not row:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Volver a transcribir")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Se llamará de nuevo a la API y se reemplazará "
                           "el texto actual."))
        combo = QComboBox()
        combo.addItems(list(core.IDIOMAS))
        actual = next((k for k, val in core.IDIOMAS.items()
                       if val == row["language"]), None)
        if actual:
            combo.setCurrentText(actual)
        v.addWidget(combo)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return

        self.btn_menu.setEnabled(False)
        self.titulo.setText(f"{nombre_visible(row)}  ·  transcribiendo…")
        self.worker = RedoWorker(row, core.IDIOMAS[combo.currentText()])
        self.worker.done.connect(self.redo_ok)
        self.worker.failed.connect(self.redo_error)
        self.worker.start()

    def redo_ok(self, _texto):
        self.btn_menu.setEnabled(True)
        self.cargar()

    def redo_error(self, msg):
        self.btn_menu.setEnabled(True)
        self.mostrar()
        QMessageBox.critical(self, "Error", msg)

    def eliminar(self):
        row = self.fila_actual()
        if not row:
            return
        dlg = DeleteDialog(row, self)
        if dlg.exec() == QDialog.Accepted:
            self.player.stop()
            db.delete(row["id"], remove_files=True,
                      remove_original=dlg.remove_original())
            self.cargar()


def main():
    db.init()
    db.set_origin("gui")
    app = QApplication(sys.argv)
    app.setApplicationName("Transcripciones")
    ventana = Historial()
    ventana.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
