#!/usr/bin/env python3
"""Offline Phase-1B resource/fixture probe. Stdout is one JSON report."""
import time
STARTED = time.perf_counter()
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import queue
import resource
import threading
import wave

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

from local_probe import Bridge, CompactSilero, LocalOwner, ROOT
from smart_hub.audio import AudioError
from smart_hub.audio_pump import AudioPump
from smart_hub.events import AudioFrame
from smart_hub.worker import SerialWorker


class PCMSource:
    """Controlled synthetic source owned by the production AudioPump thread."""
    def __init__(self):
        self.queue = queue.Queue()
        self.opened = self.closed = 0

    def __enter__(self):
        self.opened += 1
        return self

    def read_frame(self):
        value = self.queue.get(timeout=10)
        if value is None:
            raise AudioError("Kết thúc nguồn PCM giả lập.")
        return value

    def request_stop(self):
        self.queue.put(None)

    def __exit__(self, *_):
        self.closed += 1


def fixture_frames(path):
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError("Fixture không đúng định dạng PCM.")
        pcm = source.readframes(source.getnframes())
    # Padding is only fixture EOF, never padding each incoming 20 ms frame.
    pcm += b"\0" * ((-len(pcm)) % 640 + 32000)
    return [pcm[index:index + 640] for index in range(0, len(pcm), 640)]


async def run(owner_name, idle_seconds):
    factory = LocalOwner
    if owner_name == "pipecat":
        from pipecat_probe import PipecatOwner
        factory = PipecatOwner
    events = []
    cpu_started = time.process_time()
    async with SerialWorker("phase1b-vad") as worker:
        vad = await worker.call(CompactSilero)
        bridge = Bridge(factory, vad, worker, events.append)
        await bridge.start()
        startup_seconds = time.perf_counter() - STARTED
        source = PCMSource()
        lag = []
        heartbeat_stop = asyncio.Event()

        async def heartbeat():
            while not heartbeat_stop.is_set():
                target = time.perf_counter() + 0.005
                await asyncio.sleep(0.005)
                lag.append(max(0, time.perf_counter() - target))

        async with AudioPump(source_factory=lambda _: source) as pump:
            heart = asyncio.create_task(heartbeat())
            idle_wall, idle_cpu = time.perf_counter(), time.process_time()
            try:
                for index in range(math.ceil(idle_seconds / 0.020)):
                    source.queue.put(b"\0" * 640)
                    await bridge.accept(await pump.read(), pump)
                    await asyncio.sleep(max(0, idle_wall + (index + 1) * 0.020 - time.perf_counter()))
                idle_elapsed = time.perf_counter() - idle_wall
                idle_cpu_seconds = time.process_time() - idle_cpu
            finally:
                heartbeat_stop.set()
                await heart
            if events:
                raise AssertionError("Im lặng không được tạo lượt.")
            peak_queue = pump.max_pending
            dropped = pump.dropped
        fixture_results = []
        provenance_path = ROOT / "tests/fixtures/commands/provenance.json"
        provenance = json.loads(provenance_path.read_text())
        sequence = 10000
        for clip in provenance["clips"]:
            path = provenance_path.parent / clip["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != clip["sha256"]:
                raise ValueError(f"Fixture sai checksum: {clip['file']}")
            await bridge.invalidate()
            events.clear()
            for pcm in fixture_frames(path):
                sequence += 1
                await bridge.accept(AudioFrame(sequence, sequence * 320, time.monotonic(), pcm))
            await asyncio.sleep(0.02)  # Allow the framework's zero-delay stop task to finish.
            fixture_results.append({"file": clip["file"], "events": list(events),
                                    "pass": events == ["start", "end"]})
        await bridge.close()
        await asyncio.sleep(0)
    pending = [task.get_name() for task in asyncio.all_tasks()
               if task is not asyncio.current_task() and not task.done()]
    alive = [thread.name for thread in threading.enumerate() if thread.name.startswith("phase1b")]
    result = {
        "owner": owner_name,
        "synthetic_only": True,
        "startup_seconds": startup_seconds,
        "rss_peak_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "idle_seconds": idle_elapsed,
        "idle_cpu_seconds": idle_cpu_seconds,
        "idle_cpu_percent_one_core": idle_cpu_seconds / idle_elapsed * 100,
        "event_loop_lag_p95_ms": sorted(lag)[math.ceil(len(lag) * .95) - 1] * 1000,
        "event_loop_lag_max_ms": max(lag) * 1000,
        "total_cpu_seconds": time.process_time() - cpu_started,
        "audio_pump": {"opened": source.opened, "closed": source.closed,
                       "peak_frames": peak_queue, "dropped": dropped},
        "fixtures": fixture_results,
        "pending_tasks": pending,
        "remaining_worker_threads": alive,
        "packages": {name: importlib.metadata.version(name) for name in
                     (["numpy", "onnxruntime", "pipecat-ai"] if owner_name == "pipecat"
                      else ["numpy", "onnxruntime"])},
    }
    result["pass"] = (all(row["pass"] for row in fixture_results) and not pending
                      and not alive and not dropped and source.opened == source.closed == 1)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", choices=["local", "pipecat"], default="local")
    parser.add_argument("--idle-seconds", type=float, default=3)
    args = parser.parse_args()
    if not math.isfinite(args.idle_seconds) or not 1 <= args.idle_seconds <= 30:
        parser.error("idle-seconds cần trong 1–30 giây.")
    result = asyncio.run(run(args.owner, args.idle_seconds))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["pass"] else 1)
