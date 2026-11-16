"""Control window: set up a session, start/stop, grab, see grabs as they land, build the notes.

Hotkeys run the CLI in separate processes and append to grabs.jsonl; this window polls the
session folder, so both paths stay in sync without IPC.
"""
from __future__ import annotations

import subprocess
import sys
import time

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from . import core


class Worker(QThread):
    done = Signal(object, object)  # result, error

    def __init__(self, fn, *a, **kw):
        super().__init__()
        self.fn, self.a, self.kw = fn, a, kw

    def run(self):
        try:
            self.done.emit(self.fn(*self.a, **self.kw), None)
        except Exception as e:  # surfaced in the status line
            self.done.emit(None, e)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("scnotes")
        self.setWindowIcon(QIcon.fromTheme("media-record"))
        self.cfg = core.load_config()
        self.workers: list[Worker] = []
        self._grab_count = -1
        self._t0 = None

        root = QVBoxLayout(self)

        setup = QGroupBox("Session")
        f = QFormLayout(setup)
        self.title = QLineEdit(placeholderText="e.g. HF audio-to-audio walkthrough")
        f.addRow("Title", self.title)
        self.scene = QComboBox()
        self.screen = QComboBox()
        self.webcam = QComboBox()
        self.mic = QComboBox()
        for w in (self.scene, self.screen, self.webcam, self.mic):
            w.setEditable(True)
        f.addRow("Scene", self.scene)
        f.addRow("Screen source", self.screen)
        f.addRow("Webcam", self.webcam)
        f.addRow("Mic", self.mic)
        self.blur = QCheckBox("Blur webcam background")
        self.blur.setChecked(self.cfg["blur_webcam"])
        self.blur_strength = QSlider(Qt.Horizontal, minimum=1, maximum=20, value=int(self.cfg["blur_strength"]))
        bl = QHBoxLayout()
        bl.addWidget(self.blur)
        bl.addWidget(self.blur_strength)
        f.addRow(bl)
        self.noise = QCheckBox("Noise suppression on mic (RNNoise)")
        self.noise.setChecked(self.cfg["noise_suppression"])
        f.addRow(self.noise)
        self.apply_btn = QPushButton("Apply to OBS")
        self.apply_btn.clicked.connect(self.apply)
        refresh = QPushButton("Reload OBS sources")
        refresh.clicked.connect(self.load_sources)
        r = QHBoxLayout()
        r.addWidget(refresh)
        r.addWidget(self.apply_btn)
        f.addRow(r)
        root.addWidget(setup)

        ctl = QHBoxLayout()
        self.rec_btn = QPushButton("● Start")
        self.rec_btn.setMinimumHeight(40)
        self.rec_btn.clicked.connect(self.toggle)
        self.grab_btn = QPushButton("Grab")
        self.grab_btn.clicked.connect(lambda: self.run(core.grab, after=self.refresh))
        self.ann_btn = QPushButton("Grab + annotate")
        self.ann_btn.clicked.connect(lambda: self.run(core.grab, annotate=True, after=self.refresh))
        self.clock = QLabel("--:--")
        self.clock.setStyleSheet("font: bold 16pt monospace")
        for w in (self.rec_btn, self.grab_btn, self.ann_btn, self.clock):
            ctl.addWidget(w)
        root.addLayout(ctl)
        hk = self.cfg["hotkeys"]
        root.addWidget(QLabel(f"Hotkeys (work from any window): {hk['toggle']} start/stop · {hk['grab']} grab · "
                              f"{hk['annotate']} grab + annotate"))

        self.grabs = QListWidget()
        self.grabs.setViewMode(QListWidget.IconMode)
        from PySide6.QtCore import QSize
        self.grabs.setIconSize(QSize(200, 120))
        self.grabs.setResizeMode(QListWidget.Adjust)
        self.grabs.itemDoubleClicked.connect(lambda it: subprocess.Popen(["xdg-open", it.data(Qt.UserRole)]))
        root.addWidget(self.grabs, 1)

        bottom = QHBoxLayout()
        self.build_btn = QPushButton("Build notes")
        self.build_btn.clicked.connect(self.build)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.clicked.connect(lambda: self.session and subprocess.Popen(["xdg-open", str(self.session)]))
        bottom.addWidget(self.build_btn)
        bottom.addWidget(self.open_btn)
        root.addLayout(bottom)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.session = core.current_session()
        self.load_sources()
        self.timer = QTimer(self, interval=1000, timeout=self.refresh)
        self.timer.start()
        self.refresh()
        self.resize(720, 760)

    # -- helpers
    def say(self, msg):
        self.status.setText(msg)

    def run(self, fn, *a, after=None, **kw):
        w = Worker(fn, *a, **kw)
        self.workers.append(w)

        def fin(res, err):
            self.workers.remove(w)
            if err:
                self.say(f"Error: {err}")
            elif after:
                after(res)
        w.done.connect(fin)
        w.start()

    def collect(self):
        self.cfg.update(scene=self.scene.currentText(), screen_source=self.screen.currentText(),
                        webcam_source=self.webcam.currentText(), mic_source=self.mic.currentText(),
                        blur_webcam=self.blur.isChecked(), blur_strength=self.blur_strength.value(),
                        noise_suppression=self.noise.isChecked())
        core.save_config(self.cfg)
        return self.cfg

    # -- actions
    def load_sources(self):
        def fill(res):
            scenes, inputs = res
            for box, items, key in ((self.scene, scenes, "scene"), (self.screen, inputs, "screen_source"),
                                    (self.webcam, inputs, "webcam_source"), (self.mic, inputs, "mic_source")):
                box.clear()
                box.addItems(items)
                box.setCurrentText(self.cfg[key])
            self.say("Connected to OBS.")

        def fetch():
            o = core.OBS(self.cfg)
            return o.scenes(), o.inputs()
        for box, key in ((self.scene, "scene"), (self.screen, "screen_source"), (self.webcam, "webcam_source"),
                         (self.mic, "mic_source")):
            box.setCurrentText(self.cfg[key])
        self.run(fetch, after=fill)

    def apply(self):
        self.run(core.ensure_filters, self.collect(), after=lambda res: self.say("; ".join(res)))

    def toggle(self):
        if self.recording():
            self.run(core.stop, self.collect(), after=lambda d: (self.say(f"Saved to {d}  ·  press Build notes"), self.refresh()))
        else:
            title = self.title.text().strip() or time.strftime("Screencast %H%M")
            self.run(core.start, title, self.collect(), after=lambda d: (self.say(f"Recording to {d}"),
                                                                          self.refresh()))

    def build(self):
        if not self.session:
            return self.say("No session.")
        if self.recording():
            return self.say("Stop the recording first.")
        self.build_btn.setEnabled(False)
        self.say("Transcribing and building notes...")

        def fin(path):
            self.build_btn.setEnabled(True)
            self.say(f"Notes: {path}")
        self.run(core.build, self.session, self.collect(), after=fin)

    def recording(self) -> bool:
        return bool(self.session and core.read_session(self.session).get("stopped") is None)

    def refresh(self, *_):
        self.session = core.current_session()
        rec = self.recording()
        self.rec_btn.setText("■ Stop" if rec else "● Start")
        self.rec_btn.setStyleSheet("background:#c0392b;color:white;font-weight:bold" if rec else "")
        self.grab_btn.setEnabled(rec)
        self.ann_btn.setEnabled(rec)
        self.build_btn.setEnabled(bool(self.session) and not rec and not self.workers)
        if rec:
            s = core.read_session(self.session)
            import datetime as dt
            el = (dt.datetime.now() - dt.datetime.fromisoformat(s["started"])).total_seconds() * 1000
            self.clock.setText(core.mmss(int(el)))
            self.setWindowTitle(f"scnotes ● {s['title']}")
        else:
            self.clock.setText("--:--")
            self.setWindowTitle("scnotes")
        grabs = core.read_grabs(self.session) if self.session else []
        if len(grabs) != self._grab_count:
            self._grab_count = len(grabs)
            self.grabs.clear()
            for g in grabs:
                path = str(self.session / g["file"])
                it = QListWidgetItem(QIcon(QPixmap(path)), f"{g['n']} · {core.mmss(g['t_ms'])}"
                                     + (" ✎" if g["kind"] == "annotated" else ""))
                it.setData(Qt.UserRole, path)
                self.grabs.addItem(it)


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("scnotes")
    app.setDesktopFileName("scnotes")
    w = Window()
    w.show()
    return app.exec()
