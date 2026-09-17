"""Contract checks shared by both owners; no hardware or conversation backend."""
import asyncio
from pathlib import Path
import threading
import time
import sys
import socket
import tempfile
import unittest

from local_probe import Activity, Bridge, CompactSilero, LocalOwner, SpeechGate
from probe import PCMSource
from smart_hub.audio_pump import AudioPump
from smart_hub.events import AudioFrame, AudioGap
from smart_hub.worker import SerialWorker

OWNER = LocalOwner
if "--pipecat" in sys.argv:
    sys.argv.remove("--pipecat")
    from pipecat_probe import PipecatOwner
    OWNER = PipecatOwner


def frame(number, marker=0):
    return AudioFrame(number, number * 320, time.monotonic(), bytes([marker, 0]) * 320)


async def eventually(condition):
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(0.002)


class ScriptedVAD:
    def __init__(self):
        self.calls = self.resets = 0
        self.release = threading.Event()
        self.release.set()
        self.started = threading.Event()

    def reset(self):
        self.resets += 1

    def feed(self, pcm):
        self.calls += 1
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError("Test không mở khóa worker.")
        return [Activity({1: "start", 2: "end"}[pcm[0]], 512)] if pcm[0] in (1, 2) else []


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = await SerialWorker("phase1b-test").__aenter__()
        self.vad = ScriptedVAD()
        self.events = []
        self.bridge = Bridge(OWNER, self.vad, self.worker, self.events.append)
        await self.bridge.start()

    async def asyncTearDown(self):
        self.vad.release.set()
        await self.bridge.close()
        await self.worker.close()
        await asyncio.sleep(0)
        self.assertFalse(self.worker.thread.is_alive())
        self.assertEqual([t.get_name() for t in asyncio.all_tasks()
                          if t is not asyncio.current_task() and not t.done()], [])

    async def test_one_final_turn_then_three_fake_providers(self):
        await self.bridge.accept(frame(0, 1))
        await self.bridge.accept(frame(1, 1))
        await self.bridge.accept(frame(2, 2))
        await eventually(lambda: self.events.count("end") == 1)
        await self.bridge.accept(frame(3, 2))
        await asyncio.sleep(.02)
        self.assertEqual(self.events, ["start", "end"])
        calls = []
        def fake_stt(_):
            calls.append("STT")
            return "câu thử"
        def fake_llm(text):
            calls.append("LLM")
            return "đã nhận " + text
        def fake_tts(text):
            calls.append("TTS")
            return text.encode("utf-8")
        self.assertEqual(await self.bridge.response([fake_stt, fake_llm, fake_tts]),
                         "đã nhận câu thử".encode())
        self.assertEqual(calls, ["STT", "LLM", "TTS"])

    async def test_stale_provider_never_starts_the_next_stage(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def stt(_):
            started.set()
            if not release.wait(2):
                raise RuntimeError("Provider test bị kẹt.")
            return "kết quả muộn"
        def llm(text):
            calls.append(text)
        task = asyncio.create_task(self.bridge.response([stt, llm]))
        try:
            await eventually(started.is_set)
            await self.bridge.invalidate()
        finally:
            release.set()
        self.assertIsNone(await task)
        self.assertEqual(calls, [])

    async def test_provider_timeout_abandons_remaining_stages(self):
        release = threading.Event()
        calls = []
        def slow(_):
            release.wait(2)
            return "quá hạn"
        try:
            with self.assertRaises(TimeoutError):
                await self.bridge.response([slow, calls.append], timeout=.02)
        finally:
            release.set()
        self.assertEqual(calls, [])

    async def test_playback_discards_audio_and_resets_evidence(self):
        await self.bridge.playback(True)
        await self.bridge.accept(frame(0, 1))
        await self.bridge.accept(frame(1, 2))
        self.assertEqual(self.events, [])
        self.assertEqual(self.vad.calls, 0)
        await self.bridge.playback(False)
        await self.bridge.accept(frame(2, 0))
        self.assertEqual(self.vad.resets, 1)
        self.assertEqual(self.bridge.suppressed, 2)

    async def test_gap_aborts_turn_without_emitting_a_final(self):
        await self.bridge.accept(frame(0, 1))
        await self.bridge.accept(AudioGap(320, 640, 1, ("overflow",)))
        await self.bridge.accept(frame(2, 2))
        await asyncio.sleep(.02)
        self.assertEqual(self.events, ["start"])
        self.assertEqual(self.vad.resets, 2)

    async def test_late_vad_result_is_dropped_after_stop(self):
        self.vad.release.clear()
        task = asyncio.create_task(self.bridge.accept(frame(0, 1)))
        try:
            await eventually(self.vad.started.is_set)
            await self.bridge.close()
        finally:
            self.vad.release.set()
            await task
        self.assertEqual(self.events, [])
        self.assertEqual(self.bridge.stale, 1)

    async def test_late_vad_result_is_dropped_after_playback(self):
        self.vad.release.clear()
        task = asyncio.create_task(self.bridge.accept(frame(0, 1)))
        try:
            await eventually(self.vad.started.is_set)
            await self.bridge.playback(True)
        finally:
            self.vad.release.set()
            await task
        self.assertEqual(self.events, [])
        self.assertEqual(self.bridge.stale, 1)

    async def test_duplicate_frame_does_not_reach_vad(self):
        await self.bridge.accept(frame(0, 1))
        await self.bridge.accept(frame(0, 2))
        self.assertEqual(self.vad.calls, 1)
        self.assertEqual(self.bridge.stale, 1)

    async def test_overflow_is_bounded_and_invalidates_inflight_result(self):
        source = PCMSource()
        self.vad.release.clear()
        async with AudioPump(source_factory=lambda _: source, buffer_ms=60) as pump:
            source.queue.put(frame(0, 1).pcm)
            item = await pump.read()
            task = asyncio.create_task(self.bridge.accept(item, pump))
            try:
                await eventually(self.vad.started.is_set)
                for _ in range(100):
                    source.queue.put(frame(0).pcm)
                await eventually(lambda: pump.dropped == 97)
            finally:
                self.vad.release.set()
                await task
            gap = await pump.read()
            self.assertIsInstance(gap, AudioGap)
            await self.bridge.accept(gap)
            self.assertEqual(pump.max_pending, 3)
            self.assertEqual(self.events, [])
            self.assertEqual(self.bridge.stale, 1)
        self.assertEqual((source.opened, source.closed), (1, 1))

    async def test_cancellation_does_not_leave_a_late_final(self):
        await self.bridge.accept(frame(0, 1))
        self.vad.started.clear()
        self.vad.release.clear()
        task = asyncio.create_task(self.bridge.accept(frame(1, 2)))
        try:
            await eventually(self.vad.started.is_set)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await self.bridge.close()
        finally:
            self.vad.release.set()
        self.assertEqual(self.events, ["start"])


class VADTests(unittest.TestCase):
    def test_silence_short_noise_pause_and_one_end(self):
        gate = SpeechGate()
        decisions = [0.] * 50 + [1.] * 3 + [0.] * 30
        self.assertEqual([x for p in decisions if (x := gate.feed(p))], [])
        decisions = [1.] * 10 + [0.] * 10 + [1.] * 8 + [0.] * 30
        self.assertEqual([x.kind for p in decisions if (x := gate.feed(p))], ["start", "end"])

    def test_gap_resets_active_speech(self):
        gate = SpeechGate()
        for _ in range(10):
            gate.feed(1.)
        gate.reset()
        self.assertEqual([x for _ in range(30) if (x := gate.feed(0.))], [])

    def test_missing_asset_fails_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                CompactSilero(Path(directory) / "missing.onnx")

    def test_network_connections_are_blocked_in_probe(self):
        with self.assertRaisesRegex(RuntimeError, "Probe offline"):
            socket.getaddrinfo("example.com", 443)

    def test_320_to_512_preserves_samples_and_residual(self):
        import numpy as np
        vad = CompactSilero()
        inputs = []
        class Model:
            def run(self, _, values):
                inputs.extend(values["x"][0])
                return np.zeros((1, 1)), values["h"], values["c"]
        vad.model = Model()
        original = np.arange(320 * 9, dtype="<i2")
        for pcm in [original[i:i + 320].tobytes() for i in range(0, len(original), 320)]:
            vad.feed(pcm)
        np.testing.assert_array_equal(np.array(inputs), original[:2560] / 32768)
        self.assertEqual(bytes(vad.pending), original[2560:].tobytes())
        vad.reset()
        self.assertEqual(vad.pending, b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
