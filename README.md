# Narrated Screenshot Notes (`scnotes`)

Talk through what's on your screen while OBS records. Press a hotkey whenever you want the model to see exactly what
you are pointing at. When you stop, `scnotes` transcribes the narration and writes `notes.md`: the transcript with
each screenshot placed inline at the moment you took it. Hand that folder to any vision model (Claude, Gemini, GPT)
instead of a 20-minute video: it is cheaper and the frames are full resolution, so "this one here" is unambiguous.

Linux, OBS Studio 30+ with obs-websocket 5, KDE Plasma for global hotkeys (other desktops: bind the CLI commands yourself).

## What it does

| Piece | How |
|---|---|
| Recording | OBS over obs-websocket; screen + webcam picture-in-picture scene |
| Webcam background blur | [obs-backgroundremoval](https://github.com/royshil/obs-backgroundremoval) filter, `blur_background` mode |
| Mic cleanup | OBS's built-in RNNoise noise suppression filter |
| Grab | `SaveSourceScreenshot` of the screen source only (no webcam overlay), stamped with the recording offset |
| Grab + annotate | Flameshot's editor: pick a region, highlight, arrow, then Enter. Timestamp = moment of the key press |
| Transcript | AssemblyAI (sentence timestamps) or any OpenAI-compatible `/v1/audio/transcriptions` with `verbose_json` |
| Output | `notes.md` + `notes.json`, screenshots in `grabs/`, video and audio in the same folder |

## Install

```bash
uv tool install git+https://github.com/danielrosehill/Narrated-Screenshot-Notes
scnotes config      # writes ~/.config/scnotes/config.json with defaults; edit source names there or in the GUI
scnotes setup       # creates the screen source if missing, adds noise suppression + background blur
scnotes hotkeys     # KDE global shortcuts
```

Background blur needs the obs-backgroundremoval plugin. **If your OBS comes from the obsproject PPA, do not
`apt install` the plugin's `.deb`**: it depends on the distro's `libobs0t64`, and apt resolves that by removing
`obs-studio`. Extract it into the per-user plugin folder instead:

```bash
dpkg-deb -x obs-backgroundremoval_*_amd64.deb x/
P=~/.config/obs-studio/plugins/obs-backgroundremoval
mkdir -p $P/bin/64bit $P/data
cp x/usr/local/lib/obs-plugins/obs-backgroundremoval.so $P/bin/64bit/
cp -r x/usr/local/share/obs/obs-plugins/obs-backgroundremoval/* $P/data/
sudo apt install libonnxruntime1.23 libopencv-core410 libopencv-imgproc410   # its runtime deps
```

## Use

`scnotes gui` opens the control window: title, scene and source pickers, blur and noise toggles, Start/Stop, Grab,
Grab + annotate, live thumbnails of grabs, Build notes. The hotkeys work from any window (e.g. while in your browser):

| Default | Action |
|---|---|
| Meta+Alt+R | start / stop |
| Meta+Alt+G | grab screenshot |
| Meta+Alt+A | grab + annotate (Flameshot) |

The transcription key must be in the environment of whatever runs `build` (GUI or CLI): `ASSEMBLYAI_API_KEY`, or
`OPENAI_API_KEY` for the OpenAI-compatible backend.

CLI: `scnotes start "title" | grab [--note] | annotate | stop | toggle | build [DIR] | status`.

## Session folder

```
~/Videos/Screencast-Notes/2026-11-16-2330-hf-audio-to-audio/
  session.json  grabs.jsonl  grabs/001-0412.png  <recording>.mkv  audio.mp3  transcript.json  notes.md  notes.json
```

Each screenshot goes after the sentence you were saying when you pressed the key. `transcript.json` is cached, so
re-running `build` after deleting a bad grab from `grabs.jsonl` costs nothing.

## Notes

- Wayland: the first `setup` opens the desktop's screen-share picker for the new screen source; OBS keeps the choice.
  Until a screen is picked the source renders nothing; `grab` then falls back to `flameshot full`.
- OBS's own hotkeys only fire while OBS has focus on Wayland, hence KDE shortcuts that run the CLI.
- The GUI and the hotkeys share state through `$XDG_STATE_HOME/scnotes/current.json`; the GUI polls the session folder.

MIT licence.
