"""Child voice recording plan tooling, data management, and evaluation runner."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time
import wave

from .audio import RATE, pcm_stats, wav_frames
from .config import ROOT, load_config
from .stt_keyword import KeywordTrigger

CHILD_STUDY_DIR = ROOT / "recordings" / "child-study"
SESSIONS_FILE = CHILD_STUDY_DIR / "sessions.json"
LABELS_FILE = CHILD_STUDY_DIR / "labels.jsonl"
RESULTS_DIR = CHILD_STUDY_DIR / "results"
DERIVED_DIR = CHILD_STUDY_DIR / "derived"
DECISIONS_FILE = CHILD_STUDY_DIR / "decisions.md"

NEGATIVE_PRESETS = {
    1: "Maika",
    2: "Mai ca",
    3: "Mai ơi",
    4: "Mẹ ơi",
    5: "Ba ơi",
    6: "Mai đi chơi",
    7: "Con chơi nữa",
    8: "Bật đèn phòng khách",
    9: "Tắt quạt",
    10: "Em nghe",
}


def ensure_child_study_dirs(base_dir=None):
    """Ensure directory structure exists with umask 077."""
    base = Path(base_dir) if base_dir else CHILD_STUDY_DIR
    old_umask = os.umask(0o077)
    try:
        base.mkdir(parents=True, exist_ok=True)
        (base / "derived").mkdir(exist_ok=True)
        (base / "results").mkdir(exist_ok=True)
        sessions_path = base / "sessions.json"
        if not sessions_path.exists():
            sessions_path.write_text("[]\n", encoding="utf-8")
        labels_path = base / "labels.jsonl"
        if not labels_path.exists():
            labels_path.touch()
    finally:
        os.umask(old_umask)
    return base


def compute_file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def load_sessions(sessions_file=None):
    path = Path(sessions_file) if sessions_file else SESSIONS_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_session(session_info, sessions_file=None):
    path = Path(sessions_file) if sessions_file else SESSIONS_FILE
    ensure_child_study_dirs(path.parent)
    sessions = load_sessions(path)
    session_id = session_info.get("session_id")
    updated = False
    for idx, s in enumerate(sessions):
        if s.get("session_id") == session_id:
            sessions[idx] = session_info
            updated = True
            break
    if not updated:
        sessions.append(session_info)

    old_umask = os.umask(0o077)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        os.umask(old_umask)


def load_labels(labels_file=None):
    path = Path(labels_file) if labels_file else LABELS_FILE
    if not path.exists():
        return []
    labels = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    labels.append(json.loads(line))
                except Exception:
                    continue
    return labels


def save_label(entry, labels_file=None):
    path = Path(labels_file) if labels_file else LABELS_FILE
    ensure_child_study_dirs(path.parent)
    labels = load_labels(path)
    sample_id = entry.get("sample_id")
    source = entry.get("source")
    updated = False
    for idx, item in enumerate(labels):
        if (sample_id and item.get("sample_id") == sample_id) or (source and item.get("source") == source):
            labels[idx] = entry
            updated = True
            break
    if not updated:
        labels.append(entry)

    old_umask = os.umask(0o077)
    try:
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for item in labels:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        tmp.replace(path)
    finally:
        os.umask(old_umask)


def create_label_entry(sample_id, source, source_sha256, speaker_id, session_id,
                       split="pilot", label="positive", transcript_human="Maika ơi",
                       expected_events=1, distance_m=1.0, condition="quiet_normal_voice",
                       speaker_confirmed=False, review_status="captured_pending_review",
                       review_note=""):
    return {
        "sample_id": sample_id,
        "source": str(source),
        "source_sha256": source_sha256,
        "speaker_id": speaker_id,
        "speaker_confirmed": bool(speaker_confirmed),
        "session_id": session_id,
        "split": split,
        "label": label,
        "transcript_human": transcript_human,
        "expected_events": expected_events,
        "distance_m": float(distance_m),
        "condition": condition,
        "review_status": review_status,
        "review_note": review_note,
    }


def evaluate_wav(wav_path, profile="standard", aliases=(), wake_word="Maika ơi",
                 cooldown_seconds=2.0, backend=None):
    """Run full VAD + STT + KeywordTrigger on a single WAV file offline."""
    import numpy as np
    from .local_stt import LocalSTT

    path = Path(wav_path)
    if not path.is_absolute():
        path = ROOT / path

    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file WAV: {path}")

    # Read WAV metadata and verify contract
    with wave.open(str(path), "rb") as w:
        channels, sampwidth, rate, nframes, comptype, _ = w.getparams()
        if (channels, sampwidth, rate, comptype) != (1, 2, RATE, "NONE"):
            raise ValueError(f"WAV không đúng chuẩn 16kHz mono 16-bit PCM: {path}")
        raw_pcm = w.readframes(nframes)

    sha256 = hashlib.sha256(raw_pcm).hexdigest()
    stats = pcm_stats(raw_pcm)

    if backend is None:
        backend = LocalSTT(wake_profile=profile)
    else:
        backend.reset()

    events = []
    transcripts = []
    segments_count = 0
    clipped_frames = 0
    decode_seconds = 0.0
    max_decode = 0.0
    samples_seen = 0

    trigger = KeywordTrigger(wake_word, events.append, aliases, cooldown_seconds)

    def handle_segments(segments):
        nonlocal segments_count, decode_seconds, max_decode
        for s in segments:
            segments_count += 1
            if float(np.mean(np.abs(s) >= 32767 / 32768)) > 0.01:
                continue
            t0 = time.monotonic()
            text = backend.transcribe(s)
            elapsed = time.monotonic() - t0
            decode_seconds += elapsed
            max_decode = max(max_decode, elapsed)
            transcripts.append(text)
            if trigger.accept(text, segments_count, samples_seen / RATE):
                backend.reset()

    for frame in wav_frames(path):
        samples_seen += len(frame) // 2
        samples = np.frombuffer(frame, dtype="<i2").astype(np.int32)
        if float(np.mean(np.abs(samples) >= 32767)) > 0.01:
            clipped_frames += 1
            backend.reset()
            continue
        handle_segments(backend.feed(frame))

    # EOF flush
    handle_segments(backend.flush())

    audio_seconds = len(raw_pcm) / (RATE * 2)
    rtf = decode_seconds / audio_seconds if audio_seconds > 0 else 0.0

    return {
        "file": str(wav_path),
        "sha256": sha256,
        "profile": profile,
        "events": len(events),
        "transcripts": transcripts,
        "segments": segments_count,
        "clipped_frames": clipped_frames,
        "peak": stats["peak"],
        "rms": stats["rms"],
        "clipped_percent": stats["clipped_percent"],
        "audio_seconds": audio_seconds,
        "decode_seconds": decode_seconds,
        "max_decode_seconds": max_decode,
        "rtf": rtf,
    }


def evaluate_sample(sample, profiles=("standard", "sensitive"), aliases=(),
                    wake_word="Maika ơi", cooldown_seconds=2.0, preloaded_backends=None):
    """Evaluate a labeled sample dictionary on multiple profiles."""
    source = sample.get("source")
    expected_events = sample.get("expected_events", 1 if sample.get("label") == "positive" else 0)
    label = sample.get("label", "positive" if expected_events > 0 else "negative")

    evaluations = {}
    for prof in profiles:
        backend = preloaded_backends.get(prof) if preloaded_backends else None
        try:
            res = evaluate_wav(source, profile=prof, aliases=aliases, wake_word=wake_word,
                               cooldown_seconds=cooldown_seconds, backend=backend)
            events = res["events"]
            if label == "positive":
                if events == 1:
                    status = "ACCURATE"
                elif events == 0:
                    status = "MISSED"
                else:
                    status = "DUPLICATE"
            else:
                if events == 0:
                    status = "CORRECT_REJECT"
                else:
                    status = "FALSE_ALARM"

            res["status"] = status
            res["expected_events"] = expected_events
            res["is_success"] = (status in ("ACCURATE", "CORRECT_REJECT"))
            evaluations[prof] = res
        except Exception as exc:
            evaluations[prof] = {
                "file": str(source),
                "profile": prof,
                "events": 0,
                "transcripts": [],
                "status": "ERROR",
                "error": str(exc),
                "is_success": False,
                "expected_events": expected_events,
            }

    return {
        "sample_id": sample.get("sample_id"),
        "source": source,
        "source_sha256": sample.get("source_sha256"),
        "speaker_id": sample.get("speaker_id"),
        "session_id": sample.get("session_id"),
        "split": sample.get("split", "pilot"),
        "label": label,
        "transcript_human": sample.get("transcript_human"),
        "condition": sample.get("condition", "quiet_normal_voice"),
        "distance_m": sample.get("distance_m", 1.0),
        "speaker_confirmed": sample.get("speaker_confirmed", False),
        "review_status": sample.get("review_status", "captured_pending_review"),
        "evaluations": evaluations,
    }


def evaluate_dataset(samples, profiles=("standard", "sensitive"), aliases=(),
                     wake_word="Maika ơi"):
    """Evaluate a collection of labeled samples and produce aggregated metrics."""
    from .local_stt import LocalSTT

    # Preload backends once to avoid re-loading on each WAV
    backends = {}
    for prof in profiles:
        try:
            backends[prof] = LocalSTT(wake_profile=prof)
        except Exception:
            pass

    evaluated_samples = []
    for s in samples:
        evaluated_samples.append(evaluate_sample(
            s, profiles=profiles, aliases=aliases, wake_word=wake_word,
            preloaded_backends=backends
        ))

    # Aggregated metrics per profile
    metrics = {}
    for prof in profiles:
        pos_total = 0
        pos_accurate = 0
        pos_missed = 0
        pos_duplicate = 0

        neg_total = 0
        neg_correct_reject = 0
        neg_false_alarm = 0

        errors = 0
        total_decode = 0.0
        total_audio = 0.0
        max_decode = 0.0

        # Subgroup stats: (speaker, condition, split)
        groups = {}

        for item in evaluated_samples:
            res = item["evaluations"].get(prof, {})
            label = item["label"]
            status = res.get("status")

            speaker = item.get("speaker_id", "unknown")
            condition = item.get("condition", "unknown")
            split = item.get("split", "unknown")
            group_key = f"{speaker} | {condition} | {split}"
            if group_key not in groups:
                groups[group_key] = {
                    "total": 0, "pos_total": 0, "pos_accurate": 0,
                    "neg_total": 0, "neg_correct_reject": 0, "errors": 0
                }
            g = groups[group_key]
            g["total"] += 1

            if status == "ERROR":
                errors += 1
                g["errors"] += 1
                continue

            total_decode += res.get("decode_seconds", 0.0)
            total_audio += res.get("audio_seconds", 0.0)
            max_decode = max(max_decode, res.get("max_decode_seconds", 0.0))

            if label == "positive":
                pos_total += 1
                g["pos_total"] += 1
                if status == "ACCURATE":
                    pos_accurate += 1
                    g["pos_accurate"] += 1
                elif status == "MISSED":
                    pos_missed += 1
                elif status == "DUPLICATE":
                    pos_duplicate += 1
            else:
                neg_total += 1
                g["neg_total"] += 1
                if status == "CORRECT_REJECT":
                    neg_correct_reject += 1
                    g["neg_correct_reject"] += 1
                else:
                    neg_false_alarm += 1

        accuracy_pos = (pos_accurate / pos_total) if pos_total > 0 else 0.0
        frr = (pos_missed / pos_total) if pos_total > 0 else 0.0
        duplicate_rate = (pos_duplicate / pos_total) if pos_total > 0 else 0.0
        far = (neg_false_alarm / neg_total) if neg_total > 0 else 0.0
        rtf = (total_decode / total_audio) if total_audio > 0 else 0.0

        metrics[prof] = {
            "profile": prof,
            "total_samples": len(evaluated_samples),
            "positive": {
                "total": pos_total,
                "accurate": pos_accurate,
                "missed": pos_missed,
                "duplicate": pos_duplicate,
                "accurate_rate": accuracy_pos,
                "frr": frr,
                "duplicate_rate": duplicate_rate,
            },
            "negative": {
                "total": neg_total,
                "correct_reject": neg_correct_reject,
                "false_alarm": neg_false_alarm,
                "far": far,
            },
            "errors": errors,
            "performance": {
                "total_decode_seconds": total_decode,
                "total_audio_seconds": total_audio,
                "max_decode_seconds": max_decode,
                "rtf": rtf,
            },
            "groups": groups,
        }

    return {
        "timestamp": datetime.now().astimezone().isoformat(),
        "total_samples": len(evaluated_samples),
        "profiles": list(profiles),
        "metrics": metrics,
        "samples": evaluated_samples,
    }


def format_evaluation_markdown(results):
    """Generate Markdown report according to Section 9 of CHILD_VOICE_RECORDING_PLAN.md."""
    lines = []
    lines.append(f"# Báo cáo đánh giá offline: Giọng bé và người lớn")
    lines.append(f"")
    lines.append(f"- Thời điểm đánh giá: **{results.get('timestamp')}**")
    lines.append(f"- Tổng số mẫu đã kiểm tra: **{results.get('total_samples')}**")
    lines.append(f"")

    metrics = results.get("metrics", {})
    lines.append(f"## 1. So sánh tổng hợp giữa các profile")
    lines.append(f"")
    lines.append(f"| Chỉ số | standard (baseline) | sensitive | Mục tiêu pilot |")
    lines.append(f"| --- | --- | --- | --- |")

    std = metrics.get("standard", {})
    sen = metrics.get("sensitive", {})

    std_p = std.get("positive", {})
    sen_p = sen.get("positive", {})
    std_n = std.get("negative", {})
    sen_n = sen.get("negative", {})

    def pct(r):
        return f"{r * 100:.1f}%" if r is not None else "N/A"

    std_acc = f"{std_p.get('accurate', 0)}/{std_p.get('total', 0)} ({pct(std_p.get('accurate_rate'))})"
    sen_acc = f"{sen_p.get('accurate', 0)}/{sen_p.get('total', 0)} ({pct(sen_p.get('accurate_rate'))})"
    lines.append(f"| Nhận đúng 1 lần (dương) | {std_acc} | {sen_acc} | 100% yên tĩnh, ≥90% nhiễu/xa |")

    std_frr = f"{std_p.get('missed', 0)}/{std_p.get('total', 0)} ({pct(std_p.get('frr'))})"
    sen_frr = f"{sen_p.get('missed', 0)}/{sen_p.get('total', 0)} ({pct(sen_p.get('frr'))})"
    lines.append(f"| Bỏ sót FRR | {std_frr} | {sen_frr} | 0% yên tĩnh, ≤10% nhiễu/xa |")

    std_dup = f"{std_p.get('duplicate', 0)} ({pct(std_p.get('duplicate_rate'))})"
    sen_dup = f"{sen_p.get('duplicate', 0)} ({pct(sen_p.get('duplicate_rate'))})"
    lines.append(f"| Lượt trùng (>1 event) | {std_dup} | {sen_dup} | 0 lượt trùng |")

    std_far = f"{std_n.get('false_alarm', 0)}/{std_n.get('total', 0)} ({pct(std_n.get('far'))})"
    sen_far = f"{sen_n.get('false_alarm', 0)}/{sen_n.get('total', 0)} ({pct(sen_n.get('far'))})"
    lines.append(f"| Báo nhầm trên câu âm (FAR) | {std_far} | {sen_far} | 0% |")

    std_perf = std.get("performance", {})
    sen_perf = sen.get("performance", {})
    lines.append(f"| RTF giải mã CPU | {std_perf.get('rtf', 0):.3f} | {sen_perf.get('rtf', 0):.3f} | < 0.100 |")
    lines.append(f"| Thời gian STT max | {std_perf.get('max_decode_seconds', 0):.3f}s | {sen_perf.get('max_decode_seconds', 0):.3f}s | < 0.500s |")
    lines.append(f"")

    lines.append(f"## 2. Chi tiết từng nhóm thử nghiệm")
    lines.append(f"")
    lines.append(f"| Nhóm / Điều kiện | Profile | Mẫu dương đúng | Mẫu âm đúng | Lỗi xử lý |")
    lines.append(f"| --- | --- | --- | --- | --- |")

    all_groups = set()
    for prof, m in metrics.items():
        all_groups.update(m.get("groups", {}).keys())

    for g_key in sorted(all_groups):
        for prof in sorted(metrics.keys()):
            g = metrics[prof].get("groups", {}).get(g_key, {})
            pos_str = f"{g.get('pos_accurate', 0)}/{g.get('pos_total', 0)}" if g.get('pos_total') else "N/A"
            neg_str = f"{g.get('neg_correct_reject', 0)}/{g.get('neg_total', 0)}" if g.get('neg_total') else "N/A"
            err_str = str(g.get("errors", 0))
            lines.append(f"| `{g_key}` | `{prof}` | {pos_str} | {neg_str} | {err_str} |")
    lines.append(f"")

    lines.append(f"## 3. Danh sách chi tiết từng file WAV")
    lines.append(f"")
    lines.append(f"| Sample ID | Nhãn | Profile | Events | Status | Transcript STT | Decode (s) |")
    lines.append(f"| --- | --- | --- | --- | --- | --- | --- |")

    for s in results.get("samples", []):
        sid = s.get("sample_id", Path(s.get("source")).name)
        label = s.get("label")
        for prof, res in s.get("evaluations", {}).items():
            ev = res.get("events", 0)
            st = res.get("status", "UNKNOWN")
            tx = " / ".join(res.get("transcripts", [])) or "(không có text)"
            dec = f"{res.get('decode_seconds', 0):.3f}"
            lines.append(f"| `{sid}` | `{label}` | `{prof}` | {ev} | **{st}** | {tx} | {dec} |")
    lines.append(f"")

    return "\n".join(lines)


def save_evaluation_results(results, output_path=None):
    if output_path is None:
        ensure_child_study_dirs()
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = RESULTS_DIR / f"eval-{ts}.json"
    else:
        output_path = Path(output_path)

    old_umask = os.umask(0o077)
    try:
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path = output_path.with_suffix(".md")
        md_path.write_text(format_evaluation_markdown(results), encoding="utf-8")
    finally:
        os.umask(old_umask)
    return output_path
