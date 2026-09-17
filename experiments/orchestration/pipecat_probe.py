"""Minimal Pipecat 1.10.0 turn-controller bridge, no transport/providers."""
from importlib.metadata import version

from local_probe import SpeechGate


class PipecatOwner:
    def __init__(self, emit):
        if version("pipecat-ai") != "1.10.0":
            raise RuntimeError("Probe yêu cầu pipecat-ai==1.10.0 trong môi trường riêng.")
        from loguru import logger
        logger.disable("pipecat")
        from pipecat.turns.user_start import VADUserTurnStartStrategy
        from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
        from pipecat.turns.user_turn_controller import UserTurnController
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
        from pipecat.utils.asyncio.task_manager import TaskManager
        self.emit = emit
        self.tasks = TaskManager()
        self.controller = UserTurnController(
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy(enable_interruptions=False)],
                stop=[SpeechTimeoutUserTurnStopStrategy(
                    # Silence is already confirmed by the shared VAD gate.
                    # In 1.10.0 this timer is ADDITIONAL to VAD stop_secs.
                    user_speech_timeout=0.0, wait_for_transcript=False)],
            ),
            user_turn_stop_timeout=5.0,
        )
        self.controller.add_event_handler("on_user_turn_started", self._started)
        self.controller.add_event_handler("on_user_turn_stopped", self._stopped)

    async def _started(self, *_):
        self.emit("start")

    async def _stopped(self, *_):
        self.emit("end")

    async def start(self):
        from pipecat.clocks.system_clock import SystemClock
        from pipecat.processors.frame_processor import FrameProcessorSetup
        await self.controller.setup(FrameProcessorSetup(
            clock=SystemClock(), task_manager=self.tasks, pipeline_worker=None,
            audio_in_sample_rate=16000, audio_out_sample_rate=16000,
        ))
        await self.controller.start()

    async def audio(self, pcm):
        from pipecat.frames.frames import InputAudioRawFrame
        await self.controller.process_frame(InputAudioRawFrame(
            audio=pcm, sample_rate=16000, num_channels=1))

    async def activity(self, event):
        from pipecat.frames.frames import VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
        frame = (VADUserStartedSpeakingFrame(start_secs=0.256) if event.kind == "start"
                 else VADUserStoppedSpeakingFrame(stop_secs=SpeechGate().stop_seconds))
        await self.controller.process_frame(frame)

    async def close(self):
        await self.controller.cleanup()
        for task in list(self.tasks.current_tasks()):
            await self.tasks.cancel_task(task, timeout=1)
