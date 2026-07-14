# TTS voices for Angel / Devil / Arbiter

Speak Claude's responses aloud with a distinct Piper voice per speaker, in
English, German, Chinese, or Japanese. Runs in **WSL** (Windows 11 + WSLg gives
you working audio out of the box).

## Pieces
- `speak.sh` — reads a response on stdin, splits it by speaker (😇/😈/⚖️), and
  plays each segment with the matching voice.
- `voices.conf` — maps `<lang>_<speaker>` → a Piper model file. Edit paths here.
- CLAUDE.md — instructs Claude to write in the requested language and emit a
  `<!-- speak:xx -->` tag so `speak.sh` knows which language trio to use.

## One-time setup (in WSL)

1. **Install Piper** (standalone binary — simplest):
   ```bash
   mkdir -p ~/piper && cd ~/piper
   # grab the latest linux release from:
   #   https://github.com/rhasspy/piper/releases
   wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_x86_64.tar.gz
   tar -xzf piper_linux_x86_64.tar.gz          # -> ~/piper/piper/piper
   export PATH="$HOME/piper/piper:$PATH"        # add to ~/.bashrc to persist
   ```

2. **Download voice models** (each is a `.onnx` + a `.onnx.json`, keep them together):
   Browse and download from the Piper voices catalog:
   - https://huggingface.co/rhasspy/piper-voices/tree/main
   ```bash
   mkdir -p ~/piper/models && cd ~/piper/models
   # example — English trio (repeat per model you want):
   #   .../en/en_US/amy/medium/en_US-amy-medium.onnx        (+ .onnx.json)
   #   .../en/en_US/ryan/high/en_US-ryan-high.onnx          (+ .onnx.json)
   #   .../en/en_US/lessac/medium/en_US-lessac-medium.onnx  (+ .onnx.json)
   ```
   The default filenames in `voices.conf` match common catalog models for
   en/de/zh/ja — download those, or edit `voices.conf` to whatever you grabbed.
   You need **both** files per voice; `speak.sh` references the `.onnx` and Piper
   finds the `.json` beside it.

3. **Audio player**: WSLg already routes PulseAudio to Windows. Confirm:
   ```bash
   command -v paplay || sudo apt install -y pulseaudio-utils
   ```

4. **Make it executable:**
   ```bash
   chmod +x speak.sh
   ```

## Test it (before any hook)
```bash
printf '%s\n' \
  '<!-- speak:en -->' \
  '😇 Angel — the simple path holds here, tests pass.' \
  '😈 Devil — an empty list hits line 42 and it falls over. I ran it.' \
  '⚖️ Verdict — proceed, but guard the empty case.' \
  | ./speak.sh
```
You should hear three different voices in sequence. Try `speak:de`, `speak:zh`,
`speak:ja` (with matching models downloaded), or force it: `./speak.sh --lang ja`.

## Wiring it to Claude Code later (optional)
Add a `Stop` hook in your Claude Code `settings.json` that pipes your last
message into `speak.sh`. The hook receives a `transcript_path`; a tiny wrapper
reads the final assistant message from that JSONL and pipes it in. Build and
verify `speak.sh` standalone first — the hook is just the delivery mechanism.

## Honest limits
- **Voices split only on gated decisions.** A response with no 😇/😈/⚖️ markers is
  spoken entirely in the Arbiter voice — that's expected (ungated turns are
  single-voice).
- **zh/ja have fewer distinct models** than en/de, so the three speakers may sound
  similar in those languages. `voices.conf` reuses one model per language by
  default; point them at different models if you find good ones.
- **Language must match the text.** `speak.sh` trusts the `speak:xx` tag; if the
  text is English but tagged `de`, a German voice will mispronounce it. Claude
  sets tag and text together, so this only bites if you force `--lang` wrongly.
- Emoji/marker glyphs are stripped before speaking so they aren't read aloud.
