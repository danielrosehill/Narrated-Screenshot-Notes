# Build log

## 2026-11-16: obs-backgroundremoval .deb removed OBS

Laptop: Ubuntu 26.04, OBS 33.0.0 from ppa:obsproject (`33.0.0-0obsproject3~resolute`).

- `apt-get install ./obs-backgroundremoval_1.4.1.forky_amd64.deb` (Debian forky build, the only Linux asset on
  the 1.4.1 release) depends on `libobs0t64`. apt installed Ubuntu's `libobs0t64 32.1.0` and **removed obs-studio**.
  Nothing failed loudly; `obs` simply vanished from PATH.
- Fix: `apt remove obs-backgroundremoval libobs0t64`, `apt install obs-studio=33.0.0-0obsproject3~resolute`, then
  extract the .deb into `~/.config/obs-studio/plugins/obs-backgroundremoval/{bin/64bit,data}` (see README).
  The PPA's obs-studio ships `libobs.so.0`, so the plugin links fine. Runtime deps (onnxruntime 1.23, OpenCV 4.10)
  were marked manual so autoremove keeps them.
- Confirmed: filter kind `background_removal` appears in `GetSourceFilterKindList`; default settings include
  `blur_background: 0` (0 = remove background, >0 = blur strength), model `mediapipe`, CPU.

## 2026-11-16: KDE shortcuts

`.desktop` files with `X-KDE-Shortcuts` in `~/.local/share/applications` + `kbuildsycoca6` register as kglobalaccel
components (`/component/scnotes_grab_desktop`). Verified with
`gdbus call --session -d org.kde.kglobalaccel -o /component/scnotes_grab_desktop -m org.kde.kglobalaccel.Component.allShortcutInfos`
→ key code 402653255 = Meta+Alt+G. They do not show up in `kglobalshortcutsrc` until changed in System Settings.

## 2026-11-16: build verified on a synthetic session

espeak-ng narration + two fake grabs → AssemblyAI → `notes.md` with both images after the right sentences.
