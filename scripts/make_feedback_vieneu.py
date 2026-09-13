#!/usr/bin/env python3
"""Make two fixed local WAVs; never used by the listening process.

Inference adapted from Phạm Nguyễn Ngọc Bảo's VieNeu-TTS, Apache-2.0:
https://github.com/pnnbao97/VieNeu-TTS/blob/3206ed960e317e69bfe09f9d553aecbf1090f32e/src/vieneu/v3nano.py
Changes: preset-only, pinned local ONNX files, fixed phrases, no SDK/network,
no voice cloning; peak headroom and PCM WAV written with the standard library.
License is downloaded alongside the model and copied next to generated assets.
"""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import time
import wave

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["RAYON_NUM_THREADS"] = "1"

import numpy as np
import onnxruntime as ort
from sea_g2p import SEAPipeline
from download_voice import MODEL_DIR, ROOT, REVISION, SOURCE_REVISION, verify_all

RATE = 24000


class NanoVoice:
    def __init__(self, voice):
        verify_all()
        ort.disable_telemetry_events()
        self.config = json.loads((MODEL_DIR / "config.json").read_text())
        voices = json.loads((MODEL_DIR / "voices.json").read_text())["presets"]
        if voice not in voices:
            raise ValueError(f"Không có giọng {voice}. Có: {', '.join(voices)}")
        self.speaker = np.asarray(voices[voice]["speaker_emb"], dtype=np.float32)[None]
        self.style = np.asarray(voices[voice]["style"], dtype=np.float32)[None]
        self.pipeline = SEAPipeline(lang="vi")
        with np.load(MODEL_DIR / "constants.npz", allow_pickle=False) as constants:
            self.null_speaker = constants["null_spk"].astype(np.float32)[None]
            self.null_style = constants["null_style"].astype(np.float32)[None]
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.sessions = {name: ort.InferenceSession(str(MODEL_DIR / f"{name}.onnx"), options,
                            providers=["CPUExecutionProvider"])
                         for name in ["text_encoder", "duration_predictor", "vector_estimator", "codec_decoder"]}
        null_ids = np.array([[self.config["bos_id"], self.config["eos_id"]]], dtype=np.int64)
        self.null_context = self.run("text_encoder", ids=null_ids, style=self.null_style)
        self.null_mask = null_ids != self.config["pad_id"]

    def run(self, graph, **inputs):
        return self.sessions[graph].run(None, inputs)[0]

    def synthesize(self, text, seed=42):
        phonemes = self.pipeline.run(text, punc_norm=True)
        vocab = self.config["vocab"]
        unknown = set(phonemes) - vocab.keys()
        if unknown:
            raise ValueError(f"Âm vị ngoài model: {unknown}")
        ids = np.array([[self.config["bos_id"], *[vocab[c] for c in phonemes],
                         self.config["eos_id"]]], dtype=np.int64)
        mask = ids != self.config["pad_id"]
        ctx = self.run("text_encoder", ids=ids, style=self.style)
        log_seconds = float(self.run("duration_predictor", ctx=ctx, ctx_mask=mask, spk=self.speaker)[0])
        if not math.isfinite(log_seconds) or not -5 < log_seconds < math.log(4.5):
            raise ValueError("Model dự đoán độ dài không hợp lệ cho câu phản hồi.")
        frames = max(2, round(math.exp(log_seconds) * self.config.get("flow_fps", RATE / 256 / 6)))
        latent = np.random.default_rng(seed).standard_normal((1, 144, frames)).astype(np.float32)
        steps = 16
        for i in range(steps):
            t = np.array([i / steps], dtype=np.float32)
            velocity = self.run("vector_estimator", x=latent, t=t, ctx=ctx, ctx_mask=mask,
                                spk=self.speaker, style=self.style)
            unconditional = self.run("vector_estimator", x=latent, t=t, ctx=self.null_context,
                                     ctx_mask=self.null_mask, spk=self.null_speaker, style=self.null_style)
            latent += np.float32(1 / steps) * (unconditional + np.float32(3) * (velocity - unconditional))
        samples = self.run("codec_decoder", x=latent)[0, 0]
        if samples.ndim != 1 or not np.isfinite(samples).all() or not 0 < len(samples) <= 4.5 * RATE:
            raise ValueError("Audio neural không hợp lệ.")
        peak = float(np.max(np.abs(samples)))
        if peak < 0.001:
            raise ValueError("Audio neural gần im lặng.")
        # Attenuate before PCM conversion. Keep 6 dB headroom; never amplify noise.
        samples = samples * min(1, 0.5 / peak)
        fade = min(round(RATE * 0.01), len(samples) // 2)
        ramp = np.linspace(0, 1, fade)
        samples[:fade] *= ramp
        samples[-fade:] *= ramp[::-1]
        samples = np.pad(samples, (round(0.15 * RATE), round(0.15 * RATE)))
        return np.rint(samples * 32767).astype("<i2").tobytes()


def main():
    parser = argparse.ArgumentParser(description="Tạo sẵn ‘em nghe’/‘em đây’ bằng VieNeu Nano local.")
    parser.add_argument("--voice", default="Trúc Ly")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "assets")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    phrases = [("em_nghe_vieneu.wav", "Em nghe."), ("em_day_vieneu.wav", "Em đây.")]
    for name, _ in phrases:
        if (args.output_dir / name).exists():
            raise FileExistsError(f"Không ghi đè {args.output_dir / name}; chọn --output-dir khác.")
    start = time.monotonic()
    voice = NanoVoice(args.voice)
    print(f"PASS model VieNeu Nano sẵn sàng, giọng {args.voice}, {time.monotonic() - start:.2f}s", flush=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in phrases:
        start = time.monotonic()
        pcm = voice.synthesize(text, args.seed)
        with (args.output_dir / name).open("xb") as file, wave.open(file, "wb") as wav:
            wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        print(f"PASS {name}: {text} {len(pcm) / RATE / 2:.2f}s; tạo trong {time.monotonic() - start:.2f}s", flush=True)
    shutil.copyfile(MODEL_DIR / "LICENSE", args.output_dir / "VIENEU-LICENSE.txt")
    (args.output_dir / "vieneu-provenance.json").write_text(json.dumps({
        "model": "pnnbao-ump/VieNeu-TTS-v3-Nano", "model_revision": REVISION,
        "source_revision": SOURCE_REVISION, "voice": args.voice, "seed": args.seed,
        "sample_rate": RATE, "phrases": dict(phrases), "peak_ceiling_dbfs": -6.02,
        "generated_locally": True,
    }, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
