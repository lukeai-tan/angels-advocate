#!/usr/bin/env python3
# sherpa_ttsd.py — persistent Supertonic (sherpa-onnx) TTS daemon.
#
# Loads the Supertonic model ONCE and keeps it warm, so speak.sh can render
# many Japanese segments without paying the ~1-2s model-load cost each time.
# Piper (en/de/zh) does not need this — it's cheap to invoke per call — so only
# the sherpa-backed `ja` path talks to this daemon.
#
# Protocol (one request per connection, over a unix socket):
#   client -> {"sid": 0, "out": "/tmp/x.wav", "text": "...", "speed": 1.0}\n
#   server -> OK\n                (wav written to `out`)
#          or ERR <message>\n
#
# The socket path, model dir, and idle timeout are configurable via env/args so
# the same daemon works on any machine (see README_tts.md).
#
#   python3 sherpa_ttsd.py                 # start, serve until idle-timeout
#   python3 sherpa_ttsd.py --socket PATH --model-dir DIR --idle-timeout 600
#
# speak.sh auto-starts this in the background; you rarely run it by hand.
import argparse
import json
import os
import socket
import sys

DEFAULT_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sherpa_ttsd.sock"
)
DEFAULT_MODEL_DIR = os.environ.get(
    "SUPERTONIC_DIR", os.path.expanduser("~/piper/models/supertonic")
)


def log(msg):
    print(f"sherpa_ttsd: {msg}", file=sys.stderr, flush=True)


def load_tts(model_dir):
    import sherpa_onnx  # imported here so --help works without the dep

    m = model_dir
    need = [
        "duration_predictor.int8.onnx",
        "text_encoder.int8.onnx",
        "vector_estimator.int8.onnx",
        "vocoder.int8.onnx",
        "tts.json",
        "unicode_indexer.bin",
        "voice.bin",
    ]
    missing = [f for f in need if not os.path.isfile(os.path.join(m, f))]
    if missing:
        raise SystemExit(
            f"sherpa_ttsd: missing model files in {m}: {', '.join(missing)}"
        )

    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            supertonic=sherpa_onnx.OfflineTtsSupertonicModelConfig(
                duration_predictor=os.path.join(m, "duration_predictor.int8.onnx"),
                text_encoder=os.path.join(m, "text_encoder.int8.onnx"),
                vector_estimator=os.path.join(m, "vector_estimator.int8.onnx"),
                vocoder=os.path.join(m, "vocoder.int8.onnx"),
                tts_json=os.path.join(m, "tts.json"),
                unicode_indexer=os.path.join(m, "unicode_indexer.bin"),
                voice_style=os.path.join(m, "voice.bin"),
            ),
            provider="cpu",
            num_threads=int(os.environ.get("SHERPA_NUM_THREADS", "2")),
            debug=False,
        ),
    )
    return sherpa_onnx, sherpa_onnx.OfflineTts(cfg)


def bind_socket(path):
    # Clear a stale socket: if nothing is listening, the file is leftover junk.
    if os.path.exists(path):
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(path)
        except OSError:
            os.unlink(path)  # stale — safe to remove
            probe.close()
        else:
            probe.close()
            raise SystemExit(f"sherpa_ttsd: already running at {path}")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)
    return srv


def handle(conn, sherpa_onnx, tts):
    with conn:
        data = b""
        conn.settimeout(10)
        while b"\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        line = data.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
        if not line:
            return
        try:
            req = json.loads(line)
            text = req["text"]
            out = req["out"]
            sid = int(req.get("sid", 0))
            speed = float(req.get("speed", 1.0))
            if not text.strip():
                raise ValueError("empty text")
            audio = tts.generate(text, sid=sid, speed=speed)
            sherpa_onnx.write_wave(out, audio.samples, audio.sample_rate)
            conn.sendall(b"OK\n")
        except Exception as e:  # noqa: BLE001 — report any failure to the client
            conn.sendall(f"ERR {e}\n".encode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser(description="Persistent Supertonic TTS daemon")
    ap.add_argument("--socket", default=DEFAULT_SOCKET)
    ap.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    ap.add_argument(
        "--idle-timeout",
        type=float,
        default=float(os.environ.get("SHERPA_IDLE_TIMEOUT", "600")),
        help="exit after this many idle seconds (0 = never)",
    )
    args = ap.parse_args()

    sherpa_onnx, tts = load_tts(args.model_dir)
    srv = bind_socket(args.socket)
    log(f"ready: {tts.num_speakers} speakers @ {tts.sample_rate}Hz, socket {args.socket}")

    try:
        while True:
            if args.idle_timeout > 0:
                srv.settimeout(args.idle_timeout)
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                log("idle timeout reached, exiting")
                break
            handle(conn, sherpa_onnx, tts)
    finally:
        srv.close()
        try:
            os.unlink(args.socket)
        except OSError:
            pass


if __name__ == "__main__":
    main()
