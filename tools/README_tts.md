# TTS voices for Angel / Devil / Arbiter

Speak Claude's responses aloud with a distinct voice per speaker, in English,
German, Chinese, or Japanese. Runs in **WSL** (Windows 11 + WSLg gives you
working audio out of the box).

**Two TTS engines**, chosen per voice in `voices.conf`:
- **Piper** (en/de/zh) — a standalone binary; `speak.sh` pipes text to it per
  segment. Simple, no daemon.
- **Supertonic via sherpa-onnx** (ja) — Piper has no Japanese voice, so Japanese
  uses the Supertonic model through a small persistent daemon (`sherpa_ttsd.py`)
  that loads the model once and keeps it warm. It gives 10 distinct speakers, so
  Japanese finally gets a different voice per role like en/de do.

## Pieces
- `speak.sh` — reads a response on stdin, splits it by speaker (😇/😈/⚖️/✅), and
  plays each segment with the matching voice. Dispatches to Piper or the sherpa
  daemon based on the `voices.conf` value.
- `sherpa_ttsd.py` — persistent Supertonic TTS daemon (Japanese). `speak.sh`
  auto-starts it on first ja use; it self-exits after 10 min idle.
- `voices.conf` — maps `<lang>_<speaker>` → a Piper `.onnx` path **or** a
  `supertonic:<sid>` engine ref. Edit here.
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

## Japanese setup (Supertonic / sherpa-onnx)

Japanese does **not** use Piper. It uses the Supertonic model via `sherpa-onnx`.

1. **Install sherpa-onnx** into the Python you'll point `speak.sh` at:
   ```bash
   pip install sherpa-onnx
   ```
   ⚠️ **SSL cert gotcha:** on some setups pip's bundled CA bundle can't verify
   pypi's chain and you'll see `CERTIFICATE_VERIFY_FAILED`. If so, use the system
   CA bundle just for this install:
   ```bash
   SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt pip install sherpa-onnx
   ```
   The pip wheel ships the Python API + `sherpa-onnx-cli` (no
   `sherpa-onnx-offline-tts` binary) — `sherpa_ttsd.py` drives the Python API, so
   that's all you need.

2. **Download the Supertonic model** (7 files) into `~/piper/models/supertonic/`:
   ```bash
   mkdir -p ~/piper/models/supertonic && cd ~/piper/models/supertonic
   REPO=csukuangfj2/sherpa-onnx-supertonic-3-tts-int8-2026-05-11
   for f in duration_predictor.int8.onnx text_encoder.int8.onnx \
            vector_estimator.int8.onnx vocoder.int8.onnx tts.json \
            unicode_indexer.bin voice.bin; do
     curl -sL -O "https://huggingface.co/$REPO/resolve/main/$f"
   done
   ```
   Override the location with `SUPERTONIC_DIR` if you put it elsewhere.

3. **Tell `speak.sh` which Python has sherpa** (if not the default `python3`):
   ```bash
   export SHERPA_PY=/path/to/python3   # e.g. ~/miniforge3/bin/python3
   ```

4. **Speaker mapping** lives in `voices.conf` as `ja_*="supertonic:<sid>"`
   (sid 0-9). The defaults are chosen by pitch (angel=0 bright female, devil=6
   deepest male, arbiter=2 deeper female, verifier=8 mid male). Swap sids to
   taste — each is a distinct speaker.

The daemon auto-starts on first Japanese use and self-exits after 10 min idle
(`SHERPA_IDLE_TIMEOUT` to change). Nothing to manage by hand.

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
- **Voices split only on gated decisions.** A response with no 😇/😈/⚖️/✅ markers is
  spoken entirely in the Arbiter voice — that's expected (ungated turns are
  single-voice).
- **Four speakers.** 😇 Angel, 😈 Devil, ⚖️ Arbiter, and ✅ Verifier (the
  post-implementation conformance line) each route to their own voice. Set
  `<lang>_verifier` in `voices.conf`; if you lack a fourth model, point it at the
  arbiter's — it only reads the closing ✅ line.
- **zh reuses one model** for all four speakers (Piper has only `huayan` for
  Chinese), so zh speakers sound alike; point them at different models if you
  find good ones. **ja** does *not* have this limit — Supertonic's 10 speakers
  give each role a distinct voice.
- **First Japanese call is slow** (~a few seconds) while the daemon loads the
  model; subsequent calls are fast because the model stays warm.
- **Language must match the text.** `speak.sh` trusts the `speak:xx` tag; if the
  text is English but tagged `de`, a German voice will mispronounce it. Claude
  sets tag and text together, so this only bites if you force `--lang` wrongly.
- Emoji/marker glyphs are stripped before speaking so they aren't read aloud.
