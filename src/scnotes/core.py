"""Core of scnotes: drive OBS over obs-websocket, record grabs, build the notes.

A session is one folder:
    <output_root>/<YYYY-MM-DD-HHMM>-<slug>/
        session.json      title, start/stop times, video path
        grabs.jsonl       one line per screenshot: {n, t_ms, file, kind, note}
        grabs/NNN-MMSS.png
        <video>.mkv       OBS writes here (record directory is switched for the session)
        audio.mp3         extracted for transcription
        transcript.json   sentences with start/end ms
        notes.md          transcript with each screenshot inline where it was taken
        notes.json        same, machine-readable

The current session pointer lives in $XDG_STATE_HOME/scnotes/current.json so the
CLI (hotkeys) and the GUI share it without talking to each other.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "scnotes" / "config.json"
STATE_PATH = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "scnotes" / "current.json"

DEFAULTS = {
    "obs_url": "ws://localhost:4455",
    "obs_password": None,             # None: read from OBS's own websocket config
    "scene": "Screen + Webcam",
    "screen_source": "Laptop Screen",  # screenshots are taken of this source only (no webcam overlay)
    "webcam_source": "Laptop Webcam",
    "mic_source": "Mic/Aux",
    "output_root": "~/Videos/Screencast-Notes",
    "blur_webcam": True,
    "blur_strength": 15,
    "noise_suppression": True,
    "transcriber": "assemblyai",       # assemblyai | openai-compat
    "openai_compat_url": None,         # e.g. http://host:port (OpenAI-compatible /v1/audio/transcriptions)
    "openai_compat_model": "whisper-1",
    "hotkeys": {"toggle": "Meta+Alt+R", "grab": "Meta+Alt+G", "annotate": "Meta+Alt+A"},
}

OBS_CONFIG_DIRS = ["~/.config/obs-studio", "~/.var/app/com.obsproject.Studio/config/obs-studio",
                   "~/snap/obs-studio/current/.config/obs-studio"]


class ScnotesError(RuntimeError):
    pass


# ---------------------------------------------------------------- config / state

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({k: v for k, v in cfg.items()}, indent=2) + "\n")


def current_session() -> Path | None:
    if STATE_PATH.exists():
        d = Path(json.loads(STATE_PATH.read_text())["dir"])
        if d.exists():
            return d
    return None


def _set_current(d: Path | None) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if d is None:
        STATE_PATH.unlink(missing_ok=True)
    else:
        STATE_PATH.write_text(json.dumps({"dir": str(d)}))


def read_session(d: Path) -> dict:
    return json.loads((d / "session.json").read_text())


def write_session(d: Path, s: dict) -> None:
    (d / "session.json").write_text(json.dumps(s, indent=2) + "\n")


def read_grabs(d: Path) -> list[dict]:
    f = d / "grabs.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []


def notify(msg: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "scnotes", "-t", "1500", "scnotes", msg], capture_output=True)


def mmss(ms: int) -> str:
    s = int(ms // 1000)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- OBS

def _obs_password(cfg: dict) -> str:
    if cfg.get("obs_password") is not None:
        return cfg["obs_password"]
    if os.environ.get("OBS_WEBSOCKET_PASSWORD"):
        return os.environ["OBS_WEBSOCKET_PASSWORD"]
    for c in OBS_CONFIG_DIRS:
        f = Path(c).expanduser() / "plugin_config/obs-websocket/config.json"
        if f.exists():
            d = json.loads(f.read_text())
            if not d.get("server_enabled"):
                raise ScnotesError("obs-websocket is disabled: OBS > Tools > WebSocket Server Settings")
            return d.get("server_password", "") if d.get("auth_required") else ""
    return ""


class OBS:
    def __init__(self, cfg: dict):
        import obsws_python as obs
        from urllib.parse import urlparse
        u = urlparse(cfg["obs_url"])
        try:
            self.c = obs.ReqClient(host=u.hostname, port=u.port or 4455, password=_obs_password(cfg), timeout=5)
        except Exception as e:
            raise ScnotesError(f"cannot reach OBS at {cfg['obs_url']} (is OBS running?): {e}") from e

    def req(self, name: str, data: dict | None = None) -> dict:
        r = self.c.send(name, data or {}, raw=True)
        return r or {}

    def inputs(self) -> list[str]:
        return [i["inputName"] for i in self.req("GetInputList")["inputs"]]

    def scenes(self) -> list[str]:
        return [s["sceneName"] for s in self.req("GetSceneList")["scenes"]]

    def record_status(self) -> dict:
        return self.req("GetRecordStatus")


def ensure_filters(cfg: dict, obs: OBS | None = None) -> list[str]:
    """Idempotently add noise suppression to the mic and background blur to the webcam."""
    obs = obs or OBS(cfg)
    done = []
    inputs = obs.inputs()

    # Screen capture source: created on first run. On Wayland this opens the desktop's
    # screen-share picker once; OBS remembers the choice (restore token) afterwards.
    if cfg["screen_source"] not in inputs and cfg["scene"] in obs.scenes():
        kinds = obs.req("GetInputKindList")["inputKinds"]
        kind = next((k for k in ("pipewire-screen-capture-source", "xshm_input") if k in kinds), None)
        if kind:
            item = obs.req("CreateInput", {"sceneName": cfg["scene"], "inputName": cfg["screen_source"],
                                           "inputKind": kind, "inputSettings": {"ShowCursor": True},
                                           "sceneItemEnabled": True})["sceneItemId"]
            vs = obs.req("GetVideoSettings")
            obs.req("SetSceneItemTransform", {"sceneName": cfg["scene"], "sceneItemId": item, "sceneItemTransform": {
                "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": vs["baseWidth"],
                "boundsHeight": vs["baseHeight"], "positionX": 0, "positionY": 0}})
            obs.req("SetSceneItemIndex", {"sceneName": cfg["scene"], "sceneItemId": item, "sceneItemIndex": 0})
            done.append(f"created screen source '{cfg['screen_source']}' ({kind}); pick the screen in the dialog")
            inputs.append(cfg["screen_source"])

    def upsert(source, name, kind, settings, enabled):
        existing = {f["filterName"] for f in obs.req("GetSourceFilterList", {"sourceName": source})["filters"]}
        if name not in existing:
            if not enabled:
                return
            obs.req("CreateSourceFilter", {"sourceName": source, "filterName": name,
                                           "filterKind": kind, "filterSettings": settings})
            done.append(f"added {name} to {source}")
        else:
            obs.req("SetSourceFilterSettings", {"sourceName": source, "filterName": name,
                                                "filterSettings": settings, "overlay": True})
        obs.req("SetSourceFilterEnabled", {"sourceName": source, "filterName": name, "filterEnabled": enabled})
        done.append(f"{name} on {source}: {'on' if enabled else 'off'}")

    if cfg["mic_source"] in inputs:
        upsert(cfg["mic_source"], "scnotes noise suppression", "noise_suppress_filter_v2",
               {"method": "rnnoise"}, cfg["noise_suppression"])
    else:
        done.append(f"mic source '{cfg['mic_source']}' not found; skipped noise suppression")

    if cfg["webcam_source"] in inputs:
        kinds = obs.req("GetSourceFilterKindList").get("sourceFilterKinds", [])
        bg = next((k for k in kinds if "background" in k.lower()), None)
        if bg:
            upsert(cfg["webcam_source"], "scnotes background blur", bg,
                   {"blur_background": int(cfg["blur_strength"])}, cfg["blur_webcam"])
        elif cfg["blur_webcam"]:
            done.append("background blur unavailable: install the obs-backgroundremoval plugin and restart OBS")
    else:
        done.append(f"webcam source '{cfg['webcam_source']}' not found; skipped blur")
    return done


# ---------------------------------------------------------------- session lifecycle

def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "session"


def start(title: str, cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    if current_session() and read_session(current_session()).get("stopped") is None:
        raise ScnotesError(f"a session is already running: {current_session()}")
    obs = OBS(cfg)
    if obs.record_status().get("outputActive"):
        raise ScnotesError("OBS is already recording; stop it first")
    if cfg["scene"] in obs.scenes():
        obs.req("SetCurrentProgramScene", {"sceneName": cfg["scene"]})
    ensure_filters(cfg, obs)
    now = dt.datetime.now()
    d = Path(cfg["output_root"]).expanduser() / f"{now:%Y-%m-%d-%H%M}-{slugify(title)}"
    (d / "grabs").mkdir(parents=True, exist_ok=True)
    prev_dir = obs.req("GetRecordDirectory").get("recordDirectory")
    obs.req("SetRecordDirectory", {"recordDirectory": str(d)})
    obs.req("StartRecord")
    write_session(d, {"title": title, "started": now.isoformat(timespec="seconds"), "stopped": None,
                      "scene": cfg["scene"], "screen_source": cfg["screen_source"],
                      "previous_record_directory": prev_dir, "video": None})
    _set_current(d)
    notify(f"Recording: {title}")
    return d


def grab(annotate: bool = False, note: str | None = None, cfg: dict | None = None) -> dict:
    """Screenshot at the current recording offset. annotate=True opens Flameshot's editor
    (region select, highlight, arrows); the timestamp is the moment of the key press."""
    cfg = cfg or load_config()
    d = current_session()
    if not d:
        raise ScnotesError("no session running")
    obs = OBS(cfg)
    st = obs.record_status()
    if not st.get("outputActive"):
        raise ScnotesError("OBS is not recording")
    t_ms = int(st.get("outputDuration", 0))
    n = len(read_grabs(d)) + 1
    f = d / "grabs" / f"{n:03d}-{mmss(t_ms).replace(':', '')}.png"
    if annotate:
        if not shutil.which("flameshot"):
            raise ScnotesError("flameshot not installed")
        r = subprocess.run(["flameshot", "gui", "--raw"], capture_output=True)
        if not r.stdout:
            notify("Annotated grab cancelled")
            return {}
        f.write_bytes(r.stdout)
    else:
        try:
            obs.req("SaveSourceScreenshot", {"sourceName": read_session(d)["screen_source"], "imageFormat": "png",
                                             "imageFilePath": str(f)})
        except Exception:
            # Screen source not rendering (e.g. screen not picked yet): fall back to a desktop screenshot.
            r = subprocess.run(["flameshot", "full", "--raw"], capture_output=True) if shutil.which("flameshot") else None
            if not r or not r.stdout:
                raise ScnotesError("screenshot failed: screen source not rendering and no flameshot fallback")
            f.write_bytes(r.stdout)
    g = {"n": n, "t_ms": t_ms, "file": f"grabs/{f.name}", "kind": "annotated" if annotate else "screen",
         "note": note}
    with open(d / "grabs.jsonl", "a") as fh:
        fh.write(json.dumps(g) + "\n")
    notify(f"Grab {n} at {mmss(t_ms)}")
    return g


def stop(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    d = current_session()
    if not d:
        raise ScnotesError("no session running")
    obs = OBS(cfg)
    s = read_session(d)
    if obs.record_status().get("outputActive"):
        s["video"] = obs.req("StopRecord").get("outputPath")
    if s.get("previous_record_directory"):
        time.sleep(1)
        obs.req("SetRecordDirectory", {"recordDirectory": s["previous_record_directory"]})
    s["stopped"] = dt.datetime.now().isoformat(timespec="seconds")
    write_session(d, s)
    notify("Recording stopped")
    return d


def toggle(cfg: dict | None = None) -> str:
    d = current_session()
    if d and read_session(d).get("stopped") is None:
        stop(cfg)
        return f"stopped {d}"
    title = dt.datetime.now().strftime("Screencast %H%M")
    return f"started {start(title, cfg)}"


# ---------------------------------------------------------------- transcription + build

def _extract_audio(d: Path, video: str) -> Path:
    out = d / "audio.mp3"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                        "-b:a", "64k", str(out)], check=True)
    return out


def _transcribe_assemblyai(audio: Path) -> list[dict]:
    import requests
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise ScnotesError("ASSEMBLYAI_API_KEY not set (run under your secrets wrapper)")
    h = {"authorization": key}
    base = "https://api.assemblyai.com/v2"
    with open(audio, "rb") as fh:
        url = requests.post(f"{base}/upload", headers=h, data=fh, timeout=600).json()["upload_url"]
    tid = requests.post(f"{base}/transcript", headers=h, json={"audio_url": url, "punctuate": True,
                                                                "format_text": True}, timeout=60).json()["id"]
    while True:
        r = requests.get(f"{base}/transcript/{tid}", headers=h, timeout=60).json()
        if r["status"] == "completed":
            break
        if r["status"] == "error":
            raise ScnotesError(f"AssemblyAI: {r.get('error')}")
        time.sleep(3)
    sents = requests.get(f"{base}/transcript/{tid}/sentences", headers=h, timeout=60).json()["sentences"]
    return [{"text": s["text"], "start": s["start"], "end": s["end"]} for s in sents]


def _transcribe_openai_compat(audio: Path, cfg: dict) -> list[dict]:
    import requests
    if not cfg.get("openai_compat_url"):
        raise ScnotesError("set openai_compat_url in the config")
    with open(audio, "rb") as fh:
        r = requests.post(cfg["openai_compat_url"].rstrip("/") + "/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', 'none')}"},
                          files={"file": fh}, data={"model": cfg["openai_compat_model"],
                                                    "response_format": "verbose_json"}, timeout=1800)
    r.raise_for_status()
    segs = r.json().get("segments")
    if not segs:
        raise ScnotesError("endpoint returned no segments (needs verbose_json support)")
    return [{"text": s["text"].strip(), "start": int(s["start"] * 1000), "end": int(s["end"] * 1000)} for s in segs]


def build(d: Path | None = None, cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    d = d or current_session()
    if not d:
        raise ScnotesError("no session given")
    s = read_session(d)
    video = s.get("video") or next((str(p) for p in sorted(d.glob("*.mkv")) + sorted(d.glob("*.mp4"))), None)
    if not video:
        raise ScnotesError(f"no recording found in {d}")
    tfile = d / "transcript.json"
    if tfile.exists():
        sents = json.loads(tfile.read_text())
    else:
        audio = _extract_audio(d, video)
        sents = (_transcribe_assemblyai(audio) if cfg["transcriber"] == "assemblyai"
                 else _transcribe_openai_compat(audio, cfg))
        tfile.write_text(json.dumps(sents, indent=1) + "\n")
    grabs = read_grabs(d)

    # Each grab goes after the sentence being spoken when it was taken (last sentence starting before it).
    after: dict[int, list[dict]] = {}
    for g in grabs:
        idx = max([i for i, x in enumerate(sents) if x["start"] <= g["t_ms"]], default=-1)
        after.setdefault(idx, []).append(g)

    blocks: list[dict] = []          # {"type": "text", "start", "text"} | {"type": "image", ...}
    para: list[dict] = []

    def flush():
        if para:
            blocks.append({"type": "text", "start": para[0]["start"], "end": para[-1]["end"],
                           "text": " ".join(x["text"] for x in para)})
            para.clear()

    for g in after.get(-1, []):
        blocks.append({"type": "image", **g})
    for i, x in enumerate(sents):
        if para and (x["start"] - para[-1]["end"] > 2500 or len(para) >= 6):
            flush()
        para.append(x)
        if i in after:
            flush()
            blocks += [{"type": "image", **g} for g in after[i]]
    flush()

    md = [f"# {s['title']}", "",
          f"Narrated screencast recorded {s['started']}. Transcript of the narration, with each screenshot placed "
          f"where the speaker took it. Screenshots resolve what \"this\" or \"this one\" refers to.", ""]
    for b in blocks:
        if b["type"] == "text":
            md += [f"**[{mmss(b['start'])}]** {b['text']}", ""]
        else:
            cap = f"Screenshot {b['n']} at {mmss(b['t_ms'])}" + (" (annotated)" if b["kind"] == "annotated" else "")
            if b.get("note"):
                cap += f": {b['note']}"
            md += [f"![{cap}]({b['file']})", f"*{cap}*", ""]
    (d / "notes.md").write_text("\n".join(md))
    (d / "notes.json").write_text(json.dumps({"session": s, "blocks": blocks}, indent=1) + "\n")
    return d / "notes.md"


# ---------------------------------------------------------------- hotkeys (KDE)

def install_hotkeys(cfg: dict | None = None) -> list[str]:
    """KDE global shortcuts via .desktop X-KDE-Shortcuts. OBS's own hotkeys don't fire unfocused on Wayland."""
    cfg = cfg or load_config()
    exe = shutil.which("scnotes")
    if not exe:
        raise ScnotesError("scnotes not on PATH: uv tool install the repo first")
    apps = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    out = []
    for action, label in (("toggle", "start/stop recording"), ("grab", "grab screenshot"),
                          ("annotate", "grab + annotate")):
        f = apps / f"scnotes-{action}.desktop"
        f.write_text("[Desktop Entry]\nType=Application\nNoDisplay=true\n"
                     f"Name=scnotes: {label}\nExec={exe} {action}\n"
                     f"X-KDE-Shortcuts={cfg['hotkeys'][action]}\n")
        out.append(f"{cfg['hotkeys'][action]} -> {label}")
    for tool in ("kbuildsycoca6", "kbuildsycoca5"):
        if shutil.which(tool):
            subprocess.run([tool], capture_output=True)
            break
    return out
