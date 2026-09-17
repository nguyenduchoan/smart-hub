# Smart Hub / Maika Voice — Implementation Plan

> Repository reviewed: `nguyenduchoan/smart-hub`  
> Baseline reviewed: `main` at commit `f93d83aa5655d9b9daf31b1f3571b6b2439a9745`  
> Plan revision: 2026-09-17 — Phase 2 capture-only implemented and automatically tested; real-speaker acceptance pending. User requested a pause after this checkpoint; Phase 3 is not implemented.
>
> Target machine from current repository docs: Debian 12, Python 3.11, Intel i5-1135G7, ~23 GiB RAM, PipeWire/WirePlumber, internal ALC256 microphone.  
> Primary goal: evolve the existing local **“Maika ơi” wake-word prototype** into an always-on voice assistant that can:
>
> 1. remain lightweight while idle;
> 2. wake on “Maika ơi”;
> 3. hold a natural multi-turn conversation without repeating the wake word every turn;
> 4. answer general questions through a configurable LLM;
> 5. execute simple smart-home/system commands through controlled tools;
> 6. stream/voice responses with low perceived latency;
> 7. eventually allow the user to interrupt Maika while Maika is speaking;
> 8. integrate Home Assistant, Codex CLI, MCP and additional applications later;
> 9. run safely 24/7 as a non-root service on Debian.

---

## 0. Executive decision

### Current status and authorization boundary

**User update, 2026-09-13:** implementation has now been requested. During
Phase 0 the user reported that another speaker could not wake the personal
model and requested an STT-based trigger. The user selected only the full
phrase **“Maika ơi”**, then explicitly asked to finish the implementation and
instructions while leaving real-person accuracy testing to them later.

This authorizes the bounded `listen-stt` extension below inside the current
wake/acknowledgement work. It supersedes the earlier Phase 0 prohibition on
STT/dependencies only for this trigger, and defers coordinated live accuracy
testing for handoff. It does not mark the pilot passed, authorize device
control or production deployment, or prove later assistant phases complete.
See [STT handoff and manual test commands](stt-wake.md) and
[observed M1 baseline](baseline-m1.md).

**User acceptance, 2026-09-13:** the user subsequently tested `listen-stt`
and confirmed that calling “Maika ơi” works reliably for their current use
and that “em nghe” is audible. This satisfies the requested user confirmation
to proceed with Phase 1 under the existing implementation request. It does
not turn the incomplete counted pilot rows into PASS or establish accuracy
across speakers/noise conditions; those measurements remain follow-up work.

This document is a roadmap; the subsequent implementation request and user
updates above govern the authorized work. Phase 0 remains inside wake and
acknowledgement functionality. Phase 1 and later
were gated on the user's confirmation that M1 is stable; that confirmation
has now been received for the STT wake path. Phase 1 reuses this accepted
wake backend.

**User request after Phase 1, 2026-09-13:** after observing a `[WAKE]` event
in `assistant`, the user requested the words spoken after wake to be printed
on screen for checking a command. This authorizes a small diagnostic
extension: wake → fixed reply → an 8-second window for one local STT final
transcript → `[COMMAND]` → sleeping. `--wake-only` retains the prior loop.
The existing Silero/Zipformer worker owns this window; no new dependency,
turn framework, dynamic TTS, router, LLM, device control or service is added.
This supersedes the earlier restriction on command STT only for this
diagnostic flow; it does not mark the complete Phase 1B/2/3 roadmap delivered.
Acceptance criteria, validation and self-test commands:
[command-transcript.md](command-transcript.md).

**User follow-up, 2026-09-14:** keep all speech recognition local on Debian.
The current user reports acceptable command recognition for their own
voice, with child wake recognition and the delay after acknowledgement
still needing work. The diagnostic runtime now offers opt-in sensitive
wake, explicit wake-transcript logging and a shorter configurable reply
guard. Live samples from each speaker are deferred until the user says
they are ready. Five samples are diagnostic data for this STT path, not
automatic retraining. This is continued wake/command refinement and does
not certify child accuracy, later phases, device control or production.

**User follow-up, 2026-09-15:** skip additional information/sample collection
for now and perform the next step. Phase 1B is completed as an isolated
offline decision spike: select the local turn controller with the existing
Silero ONNX model. Pipecat 1.10.0 also passed the bridge tests, but conflicts
with two current core pins and adds dependency/adapter cost. Recorded
measurements and ownership: [conversation-orchestration.md](decisions/conversation-orchestration.md).
This checkpoint does not implement Phase 2 or establish new live-speaker
acceptance; no additional microphone samples were collected.

**User request, 2026-09-17:** implement the remaining plan. Phase 2 software
is now integrated using the selected local owner, with 138/138 automated
tests passing. The user then requested completing the current phase and
pausing. Work is paused after the Phase 2 software checkpoint; real-speaker
acceptance remains deferred and Phase 3 is not implemented. See
[turn capture](turn-capture.md) and [full progress](implementation-status.md).

The sequence is:

```text
Phase 0: M1 stability acceptance
  -> Phase 1: minimal runtime primitives
  -> Phase 1B: orchestration decision spike
  -> Phase 2: turn capture using the selected owner
  -> Phase 3: CPU STT/TTS benchmark and one-turn loop
  -> Phase 4: measured multi-turn conversation
  -> later tools / optional barge-in / integrations / service
```

### Keep the current M1 implementation

Do **not** rewrite the existing wake-word implementation.

The current repository already has a useful, tested foundation:

- 16 kHz / mono / 20 ms PCM capture through `arecord -D pipewire`;
- continuous local wake-word detection;
- EfficientWord-Net INT8 embeddings via ONNX Runtime;
- positive and negative local reference samples;
- threshold, cooldown and consecutive-hit protection;
- clipping detection and microphone diagnostics;
- fixed local acknowledgement WAV;
- deterministic mock and automated tests;
- no network activity in the wake-word runtime;
- pinned dependencies and checksum-controlled model downloads.

The existing `listen` command must continue to represent the **M1 wake-only mode** and must remain usable during later development.

### Add a second runtime path

After M1 acceptance, add a separate experimental runtime command; production
service readiness is assessed in Phases 8–9:

```bash
.venv/bin/python scripts/smart_hub.py assistant
```

Do not change `listen` into the full assistant.

Conceptually:

```text
listen
  └── supported M1 wake-only runtime

assistant
  └── new always-on Maika runtime
      ├── wake word
      ├── voice session
      ├── VAD / turn detection
      ├── STT
      ├── router
      ├── conversation backend
      ├── tools
      ├── TTS
      └── multi-turn lifecycle
```

This separation is important because the current M1 behaviour deliberately discards microphone audio while feedback is playing. That is correct for M1 but incompatible with eventual barge-in/full-duplex conversation.

---

# 1. Review of the current repository

## 1.1 What is already good

### Audio capture

`src/smart_hub/audio.py` is small and predictable:

```text
PipeWire
   ↓
ALSA plugin
   ↓
arecord subprocess
   ↓
16 kHz mono signed PCM
   ↓
320 samples / 20 ms frame
```

This frame size is a good basis for VAD and streaming pipelines.

Do not replace this immediately with PyAudio/sounddevice.

### Wake detector

Current neural path:

```text
1.5 s rolling window
    ↓ every 0.2 s
EfficientWord-Net INT8
    ↓
2048-d embedding
    ↓
positive references
negative references
    ↓
score + separation check
    ↓
2 consecutive hits
    ↓
WakeGate
    ↓
WakeEvent
```

This is sufficiently isolated to keep as a front-end wake detector.

### Test discipline

The project has unit tests around:

- capture contracts;
- speech segmentation;
- detector behaviour;
- one-event-per-activation behaviour;
- feedback playback;
- suppression of detector input during feedback;
- cleanup/error conditions.

Preserve that discipline for every new phase.

### Runtime safety

The current project has several behaviours that should remain design principles:

- no automatic root operations;
- no runtime model downloads;
- no accidental overwrite of enrollment files;
- checksums/revisions for downloaded model assets;
- no raw microphone recording by default;
- no open network ports by default;
- telemetry disabled for ONNX Runtime;
- explicit diagnostics instead of silently changing audio configuration.

---

## 1.2 Current gaps relative to the target product

The repository currently does **not** contain:

- a conversation state machine;
- an always-running session manager;
- a dedicated VAD suitable for natural multi-second speech turns;
- speech-to-text;
- dynamic TTS;
- a streaming response path;
- conversation memory/context;
- a router for command vs conversation;
- an LLM provider abstraction;
- tool/function calling;
- Home Assistant integration;
- Codex/MCP integration;
- interruption/barge-in;
- acoustic echo cancellation;
- service supervision/systemd deployment;
- runtime metrics;
- production recovery when the microphone/player disappears.

These gaps should be filled incrementally after M1 acceptance. The current
`listen` loop already keeps capturing until stopped; adding `assistant` is
not a substitute for diagnosing a missed second wake in that loop.

---

# 2. Target architecture

```text
                                ┌──────────────────────┐
                                │    Microphone        │
                                │  PipeWire / ALSA     │
                                └──────────┬───────────┘
                                           │
                                    20 ms PCM frames
                                           │
                                ┌──────────▼───────────┐
                                │    AudioPump         │
                                │ one long-lived input │
                                └──────────┬───────────┘
                                           │
                         ┌─────────────────┴─────────────────┐
                         │                                   │
                  assistant sleeping                  session active
                         │                                   │
                         ▼                                   ▼
                ┌────────────────┐                  ┌────────────────┐
                │ Wake Detector  │                  │ VAD / Turn     │
                │ existing M1    │                  │ Detection      │
                └───────┬────────┘                  └───────┬────────┘
                        │ "Maika ơi"                        │ utterance
                        └─────────────────┐                 ▼
                                          │        ┌────────────────┐
                                          └───────►│      STT       │
                                                   └───────┬────────┘
                                                           │ text
                                                   ┌───────▼────────┐
                                                   │     Router     │
                                                   └───┬─────┬─────┘
                                                       │     │
                                     fast/local command│     │conversation
                                                       │     │
                                      ┌────────────────┘     └─────────────┐
                                      ▼                                    ▼
                               ┌──────────────┐                    ┌────────────────┐
                               │ ToolRegistry │                    │ Conversation   │
                               │ HA/system/...│◄───────────────────│ Backend / LLM  │
                               └──────┬───────┘   tool calls       └───────┬────────┘
                                      │                                    │
                                      └────────────────┬───────────────────┘
                                                       │ text stream
                                               ┌───────▼────────┐
                                               │ Sentence/Chunk │
                                               │   Aggregator   │
                                               └───────┬────────┘
                                                       │
                                               ┌───────▼────────┐
                                               │      TTS       │
                                               │ VieNeu/Piper   │
                                               └───────┬────────┘
                                                       │ PCM
                                               ┌───────▼────────┐
                                               │   Playback     │
                                               └────────────────┘
```

After the AEC/full-duplex phase:

```text
speaker output ─────────────┐
                            ▼
                         PipeWire
                        Echo Cancel
                            ▲
microphone ─────────────────┘
                            │
                    echo-cancelled mic
                            │
                    VAD stays active while
                      Maika is speaking
                            │
                     user begins speech
                            │
                     INTERRUPT event
                            │
              cancel LLM + cancel TTS + listen
```

---

# 3. Architectural rules Codex must follow

1. **Do not break M1.**
   `listen`, enrollment, model loading and current tests remain supported.

2. **One microphone capture process for `assistant`.**
   Do not close/reopen the microphone on every turn. Audio discontinuities,
   queue limits and cancellation follow the Phase 1 contract.

3. **No raw arbitrary shell execution from voice.**
   System actions must be explicit registered tools.

4. **No secrets inside `config.json`.**
   Tokens/API keys must come from environment variables or an external mode-0600 env file.

5. **No implicit model downloads in runtime.**
   Add explicit `scripts/download_*.py` commands, pin revisions where practical, and document provenance.

6. **No raw microphone recordings by default.**
   Debug recording must require an explicit CLI flag and must write only to ignored directories.

7. **Authorize the resolved action before dispatch.**
   Validate the operation, target and arguments, then apply risk policy and
   any required confirmation before an external API can execute it. An
   uncertain write outcome must not cause automatic retry or fallback writes.

8. **Conversation mode must degrade gracefully.**
   Wake-word mode should still work if STT/LLM/Home Assistant is unavailable.

9. **Cloud use must be optional.**
   Wake word must remain local. STT/TTS should have a local implementation. LLM backend is configurable.

10. **Do not make Home Assistant the core runtime.**
    Treat Home Assistant as a smart-home tool/backend. `smart-hub` remains the voice-session owner.

11. **Choose one turn-lifecycle owner in Phase 1B.**
    Pipecat is a candidate, subject to a local bridge/dependency spike before
    Phase 2. Use either its turn strategies or the local turn controller;
    do not run both. Preserve application policy and existing device I/O.

12. **One phase = independently testable checkpoint.**
    Do not implement multiple phases in one large change.

---

# 4. Recommended target source layout

Do not rename `audio.py`, `detector.py`, `neural.py` or `feedback.py` during the first phases.

Add modules alongside them only when their phase needs them. This is a target
map, not a requirement to scaffold every module in Phase 1. The Phase 1B
decision determines whether a Pipecat bridge is needed.

```text
src/smart_hub/
├── __init__.py
├── audio.py                 # keep M1 implementation
├── cli.py
├── config.py
├── detector.py
├── engine.py
├── events.py
├── feedback.py              # keep fixed M1 feedback
├── neural.py
│
├── runtime.py               # production assistant coordinator
├── state.py                 # assistant/session state machine
├── audio_pump.py            # long-lived capture thread -> async queue
├── playback.py              # cancellable dynamic PCM playback
│
├── conversation/
│   ├── __init__.py
│   ├── session.py
│   ├── context.py
│   ├── turn.py
│   ├── pipeline.py
│   └── sentence_buffer.py
│
├── stt/
│   ├── __init__.py
│   ├── base.py
│   ├── mock.py
│   └── whisper.py
│
├── tts/
│   ├── __init__.py
│   ├── base.py
│   ├── mock.py
│   ├── vieneu.py
│   └── piper.py             # optional later
│
├── llm/
│   ├── __init__.py
│   ├── base.py
│   ├── mock.py
│   ├── ollama.py
│   └── openai_compatible.py
│
├── routing/
│   ├── __init__.py
│   ├── router.py
│   └── meta_intents.py
│
├── tools/
│   ├── __init__.py
│   ├── base.py
│   ├── registry.py
│   ├── home_assistant.py
│   ├── system_info.py
│   ├── volume.py
│   └── codex.py             # later phase
│
└── integrations/
    ├── __init__.py
    ├── pipecat_bridge.py    # only if selected in Phase 1B
    └── mcp_client.py         # later phase
```

Tests should mirror new modules:

```text
tests/
├── ... existing tests ...
├── test_state.py
├── test_audio_pump.py
├── test_turn.py
├── test_session.py
├── test_router.py
├── test_playback.py
├── test_stt.py
├── test_tts.py
├── test_tools.py
├── test_home_assistant.py
├── test_interruption.py
└── test_runtime.py
```

---

# 5. State machine

Implement the state machine explicitly. Do not infer state from scattered booleans.

These are user-visible lifecycle states. Once streaming exists, `SPEAKING`
may overlap with ongoing LLM generation and synthesis of the next sentence.
Track those activities separately under the same response generation id;
do not wait for `THINKING` to finish before allowing playback to start.

Recommended states:

```text
SLEEPING
  │
  │ WakeDetected
  ▼
ACKNOWLEDGING
  │
  ▼
LISTENING
  │ user turn complete
  ▼
TRANSCRIBING
  │
  ▼
ROUTING
  ├──────────────► EXECUTING_TOOL
  │
  └──────────────► THINKING
                       │
                       ▼
                    SPEAKING
                       │
                       ▼
                 FOLLOWUP_WAIT
                    │      │
             new speech    │ timeout / "đi ngủ"
                    │      │
                    ▼      ▼
                LISTENING  SLEEPING
```

The tool branch expands to:

```text
ROUTING -> resolve action -> validate and authorize
  -> CONFIRMING when required -> EXECUTING_TOOL -> SPEAKING
  -> clarify / reject / confirmation expires -> FOLLOWUP_WAIT or SLEEPING
```

Only the application dispatcher can enter `EXECUTING_TOOL`. LLM text or an
external framework event cannot bypass that transition. Phase 1B assigns a
single owner to turn start/end; the application owns session and policy state.

Error path:

```text
any active state
      │ recoverable error
      ▼
    ERROR
      │ short spoken/logged failure
      ▼
FOLLOWUP_WAIT or SLEEPING
```

Interruption after full-duplex phase:

```text
SPEAKING
   │ UserStartedSpeaking
   ▼
INTERRUPTING
   ├── cancel LLM generation
   ├── cancel queued TTS
   ├── stop PCM playback
   └── clear unspoken assistant text
   ▼
LISTENING
```

---

# PHASE 0 — Verify repeated wake and accept the M1 baseline

## Goal

Resolve the reported "responds only once" behaviour and establish an observed
M1 baseline before architectural changes. Hearing one fixed reply and then
returning to wake listening is expected M1 behaviour; failing to detect a
later, separate "Maika ơi" is a defect to investigate.

The original personal-model acceptance adds no STT, LLM, device control,
framework or production service. The user-authorized STT trigger below is
the limited exception; it does not process commands or conversations.

## STT trigger extension — user update 2026-09-13

- [x] Add `listen-stt` as a local CPU alternative without personal enrollment.
- [x] Use VAD and a small Vietnamese ASR model; explain and pin only the required dependencies.
- [x] Require the full “Maika ơi” phrase, preserve Vietnamese accents and word boundaries; document equivalent STT spellings.
- [x] Retain one wake event/fixed “em nghe” response, suppression during playback/echo and bounded capture buffering.
- [x] Keep existing `listen` usable for comparison/rollback.
- [x] Run automated model, mock, clipping, lifecycle and interruption checks plus a short real capture smoke check.
- [x] Provide exact setup/listen/test/mock/manual-accuracy commands in README and `docs/stt-wake.md`.
- [ ] User performs real-speaker, distance/noise and audible-response acceptance later.

The user explicitly deferred the final row; do not keep prompting them to
record during this implementation. Automated synthetic results and microphone
plumbing are separate evidence from real-speaker accuracy. The original pilot
matrix below remains the proposed manual acceptance target, not an achieved
result. CPU STT for command turns and the general async runtime still need
their own later-phase validation.

## Work

- [x] Inspect working tree and record the baseline SHA plus any local changes;
      preserve user edits. This plan may itself still be untracked.
- [x] Run all existing automated tests.
- [x] Run mock tests.
- [x] Run `check-mic`.
- [ ] Run the same-process repeated-wake test below, including a call after idle.
- [ ] Run the noise and negative pilot tests below at the intended distance.
- [ ] Compare event count with the user's count of calls and audible replies.
- [ ] If a later call fails, inspect capture health, clipping/gain, wake score,
      negative-reference score, feedback suppression and gate rearming before
      changing thresholds. A new runtime must not hide the existing defect.
- [x] Store results in `docs/baseline-m1.md`.
- [ ] A confirmed M1 defect may receive a focused fix with regression tests.
      Keep thresholds and enrollment unchanged unless diagnosis supports a
      targeted change; never overwrite enrollment. Rerun affected live tests
      with fresh calls after a fix or tuning change.
- [ ] Check gain after closing/reopening capture. Record reboot persistence as
      unverified unless a separately coordinated reboot test has been done;
      explain any intended system/audio change before doing it.
- [x] Document the unresolved project licensing decision in `docs/licensing.md`; do not assign a license on the owner's behalf. Top-level `LICENSE` remains pending the owner's choice for redistribution.
- [x] Add `docs/architecture.md` containing the existing M1 data flow and the target assistant data flow from this plan.
- [x] Record the user's explicit M1 stability confirmation before Phase 1.
  Received 2026-09-13 for STT wake and audible acknowledgement; counted/noise
  pilot rows remain incomplete as documented in `baseline-m1.md`.

## Commands

```bash
.venv/bin/python scripts/run_tests.py
python3 scripts/run_tests.py --mock
.venv/bin/python scripts/smart_hub.py listen --mock
.venv/bin/python scripts/smart_hub.py check-mic --seconds 5
# Say exactly ten separate calls in this one five-minute process.
.venv/bin/python scripts/smart_hub.py listen --seconds 300 --diagnostic --expect-events 10
# Continuous negative observation; stop with Ctrl+C after at least 30 minutes.
.venv/bin/python scripts/smart_hub.py listen --diagnostic
```

The current `--seconds` accepts at most 300; omit it for the longer run.
The continuous run requires a manual observation record. A clean exit or a
total event count alone does not prove that each intended call was detected.
No raw audio is saved; arrange each speaking window with the user first.

## M1 pilot acceptance matrix

These are initial acceptance targets, not measurements already achieved.

| Check | Procedure | PASS condition |
| --- | --- | --- |
| Capture and gain | `check-mic`, then close/reopen capture | Valid PCM; no clipping failure; selected source/gain recorded |
| Repeated wake | Ten calls in one `listen` process, at least 5 s apart and after feedback/suppression ends; include at least one call after 60 s idle | 10/10 calls each produce one event and one audible reply; no restart or extra event |
| Background noise | Twenty new calls: ten with fan/background household noise, ten with ordinary TV or speech; record source, volume setting and practical distance | At least 9/10 in each condition, zero duplicate or unmatched events; report each condition separately |
| Negative observation | At least 30 continuous minutes of representative noise/other speech, without saying the wake phrase | Zero wake events and no capture/player failure in that observed interval |

For each live row, record planned/actual call count, events matched to each
call, missed/duplicate/unmatched events, audible replies and interruptions of
the experiment. If the user did not complete a row, mark it **NOT TESTED** or
**INCOMPLETE**, not PASS. A 30-minute zero-event pilot does not establish a
long-term false-positive rate; Phase 9 extends these checks to hours and more
conditions. Any change to pilot thresholds must be explicit in the record,
not made retroactively to label a failure as PASS.

## Acceptance

- All existing automated tests pass.
- Existing M1 scope is preserved and any focused fix has regression evidence.
- Wake model still loads.
- No new dependency yet.
- Every required pilot row above passes on fresh observations.
- Documentation records exact baseline SHA, results and remaining limits.
- The user confirms M1 is stable; stop here until the next phase is requested.

## Suggested commit

```text
docs: record M1 stability acceptance before assistant runtime work
```

---

# PHASE 1 — Build runtime primitives using the accepted wake backend

**Implementation status, 2026-09-13:** `assistant` now implements this bounded
wake/acknowledgement increment with the accepted STT wake backend. Commands,
validation evidence and remaining limits: [runtime-phase1.md](runtime-phase1.md).
No Phase 1B or conversation implementation is included.
Validation: 83/83 automated tests and 38/38 hardware/model-free mock tests
pass; the real-microphone five-second smoke reached READY and closed cleanly
with zero clipping/dropped frames. Real-speaker acceptance of the new
`assistant` command remains a user-run follow-up; the accepted `listen-stt`
command stays available.

## Goal

Separate the always-on orchestration from the current synchronous `listen()` loop while preserving all M1 behaviour.

Amendment after STT wake acceptance: `assistant` uses the existing local STT
wake backend and fixed acknowledgement WAV. Preserve both `listen` and
`listen-stt`. Do not add dependencies, command transcription or dynamic replies.

## 1.1 Add typed runtime events

Extend event modelling beyond `WakeEvent`.

Add only the events needed for this phase:

```python
AudioFrame
AudioGap
WakeDetected
AssistantStateChanged
PlaybackStarted
PlaybackStopped
RuntimeErrorEvent
```

Use dataclasses. `AudioFrame` carries sequence number, absolute sample offset,
monotonic capture timestamp, sample rate/channel/format metadata and PCM.
Use capture time for turn timing, not the later queue-consumption time.

Add speech, transcript, session, tool and interruption events in their phases.
Async work must carry `session_id`, `turn_id` and `generation_id` where relevant
so results from a cancelled/expired turn can be rejected deterministically.

Do not put raw PCM into logs.

## 1.2 Add `AssistantState`

Create `src/smart_hub/state.py`.

Requirements:

- legal-transition table;
- explicit transition method;
- invalid transition raises a deterministic exception in tests;
- state changes can emit an event;
- no global singleton.

## 1.3 Add long-lived `AudioPump`

Create `src/smart_hub/audio_pump.py`.

Requirements:

- exactly one live `AlsaCapture` instance while `assistant` is running;
- capture runs in a dedicated thread;
- use a bounded thread-safe handoff; never mutate an `asyncio.Queue` directly
  from the capture thread;
- 20 ms frame contract remains;
- bound audio by duration as well as item count; start with at most 500 ms of
  pending capture audio, configurable and independent of the turn buffer;
- clean shutdown on SIGINT/SIGTERM;
- capture errors are propagated to runtime;
- bounded restart/backoff can be added in Phase 8.

One implementation is `queue.Queue(maxsize=N)` between the capture thread and
an event-loop consumer, with at most one pending `loop.call_soon_threadsafe`
wakeup. If a second `asyncio.Queue` is used, only its owning event loop may
mutate it and both queues must be bounded. An unbounded backlog of scheduled
callbacks or pending `put()` futures defeats the memory limit.

### Overflow, discontinuity and playback policy

- Keep draining the capture process even when inference is slow.
- On overflow, drop the oldest pending PCM, advance the absolute sample
  position and emit/coalesce `AudioGap` plus dropped-frame/age metrics.
- Reset stale rolling/VAD evidence after a gap. If a gap affects an active
  user turn, invalidate that turn and ask for repetition; do not send a
  truncated command to STT/tools as if it were complete.
- Preserve `WakeGate` suppression semantics: dropped feedback/echo frames
  are not evidence of silence and must not rearm the gate.
- In Phases 1–5, drain/discard mic audio during acknowledgement and dynamic
  playback plus a measured echo tail (start with the current 0.7 s). Clear
  turn/preroll buffers at that boundary; never replay queued speaker audio
  into STT after playback ends.
- Enter `LISTENING`/`FOLLOWUP_WAIT` and start their timers only after that
  guard ends. Document that the user must wait for listening readiness;
  wake phrase plus immediate command in one breath is not yet supported.
- Keep stop/error/cancellation control events independent of PCM overflow.

Do **not** call `asyncio.to_thread(source.read_frame)` 50 times/second. Use one
dedicated capture thread. Keep blocking model inference and pipe writes off
the event loop with bounded workers/queues. Cancelling an asyncio task does
not by itself stop synchronous inference; invalidate its generation, discard
late results and check cooperative cancellation between synthesis steps.
Shutdown must close the source, unblock reads and join workers with a deadline.

## 1.4 Add the new CLI command

```bash
smart_hub.py assistant
```

For Phase 1 the command only needs:

```text
SLEEPING
  ↓ wake
ACKNOWLEDGING
  ↓ fixed current WAV
SLEEPING
```

It is intentionally not a conversation yet.

## 1.5 Keep old `listen`

`listen` must continue to use existing M1 semantics.

## Tests

Add:

- state transition tests;
- illegal transition tests;
- thread-to-loop handoff and bounded pending notifications;
- queue overflow under a deliberately slow consumer; deterministic `AudioGap`;
- timestamps/offsets stay correct across dropped frames;
- feedback/echo PCM cannot become a turn or rearm the wake gate;
- stale results/control events cannot affect a newer generation;
- shutdown cleanup;
- one wake -> one acknowledgement -> sleeping;
- multiple wake cycles;
- no microphone/model required for `assistant --mock`.

Add CLI:

```bash
smart_hub.py assistant --mock
```

A mock test must be fully offline and hardware-independent.

## Acceptance

- Existing M1 test suite still passes unchanged.
- New runtime tests pass.
- `assistant --mock` runs at least three wake/sleep cycles.
- No additional STT/LLM/TTS dependencies; reuse the accepted STT wake assets.
- No additional network access.

## Suggested commit

```text
feat: add assistant runtime state machine and long-lived audio pump
```

---

# PHASE 1B — Decide orchestration before building conversation turns

**Completed, 2026-09-15:** local controller selected. Both local and Pipecat
1.10.0 passed 15 bridge checks and 10 synthetic Vietnamese fixtures in each
of three measured runs. Core regression passed 108/108. Isolated experiment,
dependencies, measurements, constraints and the single-owner decision:
[decision record](decisions/conversation-orchestration.md). Phase 2 remains
the next implementation checkpoint.

## Goal

Select one turn-lifecycle implementation before Phase 2. Pipecat is a
candidate, not a mandatory dependency already approved by this plan.
This checkpoint follows Phase 1 and was authorized by the 2026-09-15 request
to proceed with the next step while deferring additional collection.

## Bounded spike

1. Inspect a concrete Pipecat release for Python 3.11 and the existing pinned
   core dependencies. Before installing, explain purpose, transitive packages,
   expected footprint and the lighter Silero ONNX/local-controller alternative.
2. Use an isolated, ignored experiment environment so the M1 environment is
   unchanged. Download any required VAD model explicitly with provenance;
   missing assets must fail offline instead of downloading during runtime.
3. Bridge existing PCM contracts into a minimal pipeline. Use synthetic or
   approved local fixtures, fake STT/LLM/TTS, and test start/end-turn events,
   half-duplex suppression, overflow and cancellation. No real conversation
   backend, device calls, microphone recording, network transport or service.
4. Configure one explicit VAD/turn-end strategy. Do not silently activate an
   extra semantic turn model or another STT VAD with independent timing.
5. Measure startup, idle CPU/RSS and event-loop responsiveness against the
   local alternative. Check clean shutdown and M1 dependency compatibility.
6. Record version, added packages, measurements and the selected ownership
   in `docs/decisions/conversation-orchestration.md`.

## Ownership decision

| Concern | Pipecat selected | Local controller selected |
| --- | --- | --- |
| Capture, PCM format and playback process | Smart Hub AudioPump/Playback | Smart Hub AudioPump/Playback |
| Turn start/end and interruption detection | One configured Pipecat strategy through the bridge | One Smart Hub turn controller with local VAD |
| Session lifetime, wake gating and tool authorization | Smart Hub runtime/dispatcher | Smart Hub runtime/dispatcher |
| Provider work and cancellation | Bridge maps one generation/cancellation contract | Providers use that same application contract directly |
| Response text/audio bookkeeping | One application ledger updated by playback events | Same application ledger |

An application state can mirror framework events, but must not run a second
independent turn timer or interrupt decision. Phase 2 implements the selected
path; Phase 4 extends it without replacing the owner.

## Acceptance

- Offline start/stop, speech events, suppression and cancellation pass.
- Only one owner emits final turn and interruption decisions.
- Dependency/CPU/RAM costs and adapter complexity are recorded.
- M1 tests and environment remain usable.
- Decision is explicit: adopt Pipecat if the bridge is small and costs are
  acceptable; otherwise select the local controller and defer Pipecat.
- Stop at this decision; do not implement Phase 2 in the same change.

---

# PHASE 2 — Add VAD and user-turn capture

**Implemented, 2026-09-17:** `assistant --capture-only` uses one local
turn controller with Silero probabilities, bounded pre-roll/turn PCM,
no-speech and maximum-duration deadlines, gap/clipping cancellation, and
opt-in private debug WAVs. 138/138 regression tests pass; ten synthetic
Vietnamese fixtures each end automatically. Real-hardware acceptance below
remains pending; do not substitute those fixtures for new user trials.
[Usage and evidence](turn-capture.md). User requested pausing after this
checkpoint; Phase 3 is not started.

## Goal

After wake, capture a natural spoken request and determine when the user has finished speaking.

This phase does **not** need an LLM.

## 2.1 Do not reuse current `SpeechSegmenter` as the final conversation VAD

`SpeechSegmenter` is useful for the M1 enrollment/baseline path but it is RMS-based and bounded around wake-word-sized speech.

For normal conversation introduce a dedicated voice activity detector.

Use the Phase 1B decision:

- Pipecat's local Silero VAD with the chosen turn strategy, or
- a small Silero ONNX wrapper and the local turn controller.

Do not implement both paths speculatively. Keep model revision and input
contract explicit: the current Silero ONNX wrapper expects 512 samples at
16 kHz (32 ms), whereas capture supplies 320 samples (20 ms). Add a residual
buffer to assemble model windows without dropping, padding every input frame,
or duplicating samples; reset model state on discontinuity/session boundary.
The internal capture contract stays 20 ms.

Keep it behind:

```python
class VoiceActivityDetector(Protocol):
    def process(frame: bytes) -> VadResult: ...
```

## 2.2 Turn detector

Initial behaviour:

- pre-roll: 250–400 ms;
- minimum speech: ~250 ms;
- silence-to-end-turn: initially 600–800 ms;
- maximum user turn: 30 seconds;
- maximum no-speech wait after acknowledgement: 8–10 seconds.

These values must be configuration, not constants hidden in the loop.

VAD identifies speech activity, not which person spoke or whether a sentence
is semantically complete. Treat the silence timer as an initial heuristic;
include short Vietnamese replies such as `có`, `không`, `dừng`, mid-sentence
pauses and TV speech in validation. Do not count TV speech as intentional
user input merely because VAD is positive.

## 2.3 Conversation audio buffer

A `UserTurnBuffer` should collect only the current turn.

Requirements:

- bounded memory;
- append 16 kHz PCM;
- retain pre-roll;
- expose duration;
- reject pathological clipping;
- never persist audio by default;
- explicit `--debug-recordings` flag is required to save WAVs under ignored `recordings/`.

## 2.4 Phase-2 flow

```text
"Maika ơi"
    ↓
acknowledgement
    ↓
LISTENING
    ↓
VAD speech start
    ↓
collect speech
    ↓
VAD speech end
    ↓
UserTurnReady(duration, pcm)
    ↓
log only:
"[TURN] captured 2.4 s"
    ↓
SLEEPING
```

## CLI

Add:

```bash
smart_hub.py assistant --capture-only
```

Useful hardware validation:

```bash
.venv/bin/python scripts/smart_hub.py assistant --capture-only --diagnostic
```

## Tests

- speech starts/stops from synthetic VAD decisions;
- preroll retained;
- silence alone does not create a turn;
- long noise cannot grow buffer indefinitely;
- max turn duration enforced;
- debug recording off by default;
- clipping surfaced as diagnostic;
- current wake detector paused/ignored while active voice session is capturing a command;
- 320-sample capture frames produce exact model windows with residual samples retained;
- a gap invalidates the current turn instead of concatenating missing speech;
- acknowledgement/echo PCM never enters preroll or the next turn;
- short replies, pauses, TV speech and no-speech timeout have explicit outcomes.

## Acceptance

On real hardware:

1. say `Maika ơi`;
2. hear acknowledgement;
3. speak a 2–10 second sentence;
4. runtime captures exactly one turn;
5. after silence, turn ends automatically;
6. it does not require Enter or another wake word.

Repeat for ten new sentences with the user ready before capture; each complete
sentence should produce one turn without clipped first/last syllables. Include
short replies separately and report misses. A turn reaching the maximum length
is marked incomplete and requests repetition; it must not become an executable
command in a later phase.

## Suggested commit

```text
feat: capture conversational user turns with local VAD
```

---

# PHASE 3 — Local STT and dynamic Vietnamese TTS

## Goal

Achieve a complete local one-turn loop before introducing a real LLM:

```text
wake -> speech -> text -> deterministic response -> dynamic speech
```

Work in checkpoints: (3A) benchmark local STT, (3B) benchmark dynamic TTS,
then (3C) connect the deterministic loop. Use the benchmarks to choose the
model and chunk size before optimizing or committing to a latency claim.

---

## 3.1 Add STT abstraction

```python
class STTProvider(Protocol):
    async def transcribe(
        self, pcm: bytes, sample_rate: int, cancel: CancellationToken
    ) -> Transcript:
        ...
```

`Transcript` should include:

- text;
- language if available;
- duration;
- processing latency;
- optional confidence/no-speech metadata.

Implement:

```text
MockSTT
WhisperSTT
```

### Recommended local engine

Use `faster-whisper`.

Initial model benchmark candidates on the current i5:

- `base`;
- `small`;
- consider `large-v3-turbo` only after smaller models fail the Vietnamese
  quality target and an explicit CPU/disk-budget assessment justifies it.

Do not choose the model from assumptions. Benchmark on this machine.

Use multilingual models, not `.en`/English-distilled defaults. For each run,
record revision, beam size, CPU threads, input duration and cold/warm state.
Fully consume the transcription generator before recording elapsed time.
Convert S16_LE PCM to the provider's normalized float/sample-rate contract;
do not apply a second independent end-of-turn VAD with different timing.

Initial settings to test:

```text
language = vi
device = cpu
compute_type = int8
```

### Explicit model setup

Runtime must not download models.

Add:

```text
scripts/download_stt.py
```

Requirements:

- explicit model/revision in source/config;
- download only when user runs script;
- model directory ignored by Git;
- provenance file;
- print disk requirements;
- runtime fails with a useful setup instruction if model is absent.

Load the verified local directory, including tokenizer/preprocessor assets;
use offline/local-files-only options where available and test with outbound
network blocked. Passing a model size/name to a library must not silently
trigger its default download path.

---

## 3.2 Refactor VieNeu into a reusable TTS provider

The repository already contains a working CPU implementation in:

```text
scripts/make_feedback_vieneu.py
```

Do not duplicate it.

Move reusable code into:

```text
src/smart_hub/tts/vieneu.py
```

Keep the script as a thin consumer of the library.

Interfaces:

```python
class TTSProvider(Protocol):
    def synthesize(
        self, text: str, cancel: CancellationToken
    ) -> AsyncIterator[AudioChunk]: ...
```

The concrete implementation may be an async generator. `AudioChunk` includes
sample rate, channels, PCM format, chunk id and response generation id.

Implement:

```text
MockTTS
VieNeuTTS
```

The current preset `Trúc Ly` can remain the default because the existing M1 validation selected it, but make voice configurable.

### Sentence chunking

The current `NanoVoice.synthesize()` implementation rejects predicted or
decoded durations around/above 4.5 seconds. That is an existing implementation
guard, not proof that arbitrary sentences can be synthesized within it.
The observed ~0.55–0.59 s generation time covers only `em nghe`/`em đây`.
Do not extrapolate it to paragraphs or concurrent LLM use.

Implement `SentenceBuffer`:

```text
LLM/text stream
  ↓
collect until punctuation / safe limit
  ↓
short sentence
  ↓
TTS
  ↓
play while next sentence is generated
```

Even before an LLM exists, test with multi-sentence static text.

Split by Vietnamese word/punctuation boundaries and a measured size limit;
punctuation alone is insufficient for a long sentence. Preserve numbers,
abbreviations, names and negation. If duration prediction exceeds the safe
bound, subdivide with a bounded retry count instead of truncating text or
raising the guard without validation. Surface unsupported text clearly.

The present synthesizer produces an entire chunk before returning PCM.
Sentence-by-sentence output can start before the full response is ready, but
wrapping it in `AsyncIterator` does not create streaming within a chunk.
Measure first-chunk delay and pauses between chunks, including the current
leading/trailing padding; keep the user's selected voice and natural prosody.

---

## 3.3 Cancellable PCM playback

Add `src/smart_hub/playback.py`.

Requirements:

- can play PCM directly;
- does not need temporary WAV files per sentence;
- configurable output device;
- bounded asynchronous queue with a cap in seconds of audio, not just chunks;
- `stop()` immediately terminates current playback and clears queued audio;
- no zombie `aplay` processes;
- reports playback-start/playback-stop events;
- preserves existing `VoiceFeedback` for M1.

A simple initial implementation may use:

```bash
aplay -q -D <device> -t raw -f S16_LE -r 24000 -c 1
```

with stdin, but process lifecycle and cancellation must be unit tested.

Keep pipe writes off the event loop. After stop, reject any late chunk with
the cancelled generation id so the speaker cannot restart with an old answer.
Distinguish "PCM queued/written" from "audibly played": account for player and
device buffering when measuring latency or updating the spoken-text ledger.
Before AEC is accepted, follow the Phase 1 half-duplex discard/echo-tail rule.

---

## 3.4 One-turn deterministic assistant

Before adding an LLM:

```text
STT text
   ↓
if text exists
   ↓
response = "Em nghe thấy: <text>"
   ↓
dynamic VieNeu
   ↓
SLEEPING
```

Do not keep this echo behaviour after the phase; it is an acceptance scaffold.

## Metrics

Log:

```text
stt_latency_ms
tts_first_audio_ms
tts_total_ms
user_turn_seconds
```

No transcript persistence by default.

## CPU benchmark and quality gate

Use at least 20 new Vietnamese utterances of roughly 2, 5 and 10 seconds,
covering questions, short replies, names, numbers and negation; record noise
condition and distance. Arrange live trials with the user, process audio in
RAM and save only metrics unless recording is explicitly enabled. Reusable
approved fixtures may supplement these trials but do not replace live evidence.

| Measurement | Required evidence / initial target |
| --- | --- |
| STT quality | At least 18/20 preserve the intended meaning; list name/number/negation errors separately. Report normalized CER or a documented human comparison; keep Vietnamese accents |
| TTS quality | At least 20 short/long/mixed-content chunks; no crash/truncation, and user accepts clarity/naturalness for at least 18/20; include a multi-sentence response |
| Processing time | Cold initialization separately; warm STT P50/P90 and real-time factor (processing time / input duration); TTS P50/P90 first PCM, total time and inter-chunk gaps |
| End-to-end delay | Speech end -> turn decision -> final STT -> first TTS PCM -> first audible output; include silence detection and playback buffering |
| Resources | Idle and active process CPU/RSS, peak memory, audio gaps, event-loop lag; note other workloads and CPU thread settings |
| Overlap | TTS plus playback and capture now; repeat with actual LLM generation in Phase 4, using the same machine workload |

Warm STT P50 real-time factor below 1 and a first audible reply P50 below 2.5 s
from **end of speech** are initial performance targets, not guaranteed i5
capabilities. Record actual P50/P90 even when targets fail. If quality passes
but latency misses, mark performance acceptance pending: optimize the measured
bottleneck or have the user explicitly accept a documented slower profile.
An end-to-end target must not be weakened by starting the clock after VAD or
excluding the first-sentence wait. Define the audible-timing measurement
method; subprocess start time alone is not acoustic output evidence.

Keep models warm during a session and bound model workers/threads to avoid
CPU oversubscription. Record the residency policy and memory cost during idle;
low idle CPU does not imply the loaded STT/TTS models release their RAM.

## Acceptance

Real hardware:

> Maika ơi  
> "Hôm nay tôi muốn kiểm tra hệ thống"

Expected:

- transcript is printed in diagnostic mode;
- Maika dynamically says a response containing the recognized sentence;
- no pre-generated WAV except initial acknowledgement;
- no cloud required.

Additionally:

- quality and resource evidence above is recorded in `docs/benchmarks/cpu-voice.md`;
- performance meets the targets or an explicitly accepted slower profile;
- silence/non-speech noise fixtures do not produce a response, and incomplete turns are rejected;
- cancellation, queue limits and no-network/no-recording defaults pass;
- first spoken output and multi-sentence playback are verified on hardware.

## Suggested commits

```text
feat: add local faster-whisper STT provider
feat: refactor VieNeu into dynamic cancellable TTS
feat: complete one-turn local voice loop
```

---

# PHASE 4 — Multi-turn conversation with the selected orchestrator

## Goal

Turn the one-shot voice command path into a conversation resembling a desktop voice assistant.

Required behaviour:

```text
User: Maika ơi
Maika: Em nghe.
User: Codex là gì?
Maika: ...
User: Nó có chạy trên Debian không?
Maika: ...
User: Thế có thể điều khiển nó từ máy này không?
Maika: ...
```

Only the first turn requires the wake word.

---

## 4.1 Introduce `ConversationSession`

```python
ConversationSession:
    id
    started_at
    last_activity
    messages
    last_route
    last_tool_result
    turn_count
    cancelled
```

Keep session memory in RAM first.

Do not add a database yet.

Default lifecycle:

- wake starts session;
- after assistant response, follow-up listening begins automatically;
- session idle timeout: configurable, initial 20–30 seconds;
- phrases such as `thôi`, `dừng`, `đi ngủ`, `cảm ơn, thôi nhé` can end the session;
- session also ends after a configurable max number of turns or hard lifetime to avoid accidental indefinite listening.

The follow-up timer starts after audible playback and the half-duplex echo
guard end; generation time and playback time must not consume that window.
On expiry/cancel, increment the generation id and reject late STT/LLM/TTS
results. Bound context by turns, tokens and tool-result size; do not retain
the entire session indefinitely or treat unspoken text as heard by the user.

---

## 4.2 LLM abstraction

```python
class ConversationBackend(Protocol):
    async def stream_response(
        self,
        context: ConversationContext,
        tools: list[ToolDefinition],
        cancel: CancellationToken,
    ) -> AsyncIterator[ConversationEvent]:
        ...
```

Events must distinguish:

```text
TextDelta
TextComplete
ToolCallRequested
ToolCallCompleted
BackendError
```

Provide:

```text
MockConversationBackend
OllamaBackend
OpenAICompatibleBackend
```

The runtime must not depend on one vendor SDK.

For Phase 4, implement one real backend after checking its Vietnamese quality
and CPU profile. Automated tests use mocks; hardware acceptance must also use
that real backend. Additional providers can wait.

For a local LLM, start with a small quantized model chosen by measured quality,
TTFT and concurrent STT/TTS cost; enough RAM to load it does not guarantee
conversational latency. Record model/revision, context/output limits, threads,
timeouts and warm/cold residency policy. A cloud text backend is optional and
requires an explicit choice about sending transcripts/context; never silently
switch to it on local errors or send microphone audio.

---

## 4.3 Extend the Phase 1B ownership decision

Read `docs/decisions/conversation-orchestration.md`. The framework decision
and bridge spike must already be complete before Phase 2; do not introduce a
second turn controller or framework migration in this phase.

```text
existing AudioPump
     ↓
selected turn owner (Pipecat bridge OR local controller)
     ↓
session/context + STT/backend/TTS adapters
     ↓
existing Playback and one spoken-text ledger
```

If Pipecat was selected, extend the tested bridge. If the local controller was
selected, keep it and leave Pipecat out of runtime dependencies. Any later
change of orchestrator is a separate decision and migration checkpoint.
Daily/LiveKit/cloud transports remain unnecessary for this single-host path.

---

## 4.4 Conversation prompt/runtime policy

System behaviour should be concise for voice.

Guidelines:

- conversational Vietnamese by default;
- short spoken answers unless user asks for detail;
- never read Markdown syntax aloud;
- summarize code/log/tool output before TTS;
- avoid reading long URLs;
- ask for confirmation before risky side effects;
- if a tool takes time, acknowledge briefly rather than producing silence;
- do not claim an action succeeded until tool result confirms it.

---

## 4.5 Streaming response path

Do not wait for the full LLM response before TTS.

```text
LLM tokens
   ↓
SentenceBuffer
   ↓ first sentence
TTS sentence 1 ───────► speaker
   │
   ├─ simultaneously continue receiving sentence 2
   ▼
TTS sentence 2
```

Keep a bounded text/audio ledger: generated, queued, currently playing and
completed chunks, all tagged with generation id. Cap queued text/audio and
backpressure the producer so a slow CPU cannot accumulate whole paragraphs.
Only completed audible chunks count as definitely spoken. If playback is
stopped mid-chunk, mark it partial; do not claim exact word-level alignment
without timing evidence. Preserve tool outcomes separately from spoken text.

---

## Tests

- conversation id remains stable across turns;
- pronoun/follow-up context passed to backend;
- timeout ends session;
- "dừng/đi ngủ" ends session;
- session never persists raw PCM;
- backend cancellation works;
- sentence buffer flushes correctly;
- the timer starts after playback/echo guard and repeated follow-up cycles work;
- old-generation PCM/text cannot reappear after cancellation or timeout;
- context and queued output stay bounded under a slow backend/synthesizer;
- `assistant --mock` runs a scripted 3-turn conversation;
- M1 tests remain green.

## Acceptance

A 5-turn spoken conversation can occur after one wake phrase.

Test that conversation with the selected real backend and natural Vietnamese
follow-ups. Repeat Phase 3 latency/resource measurements while local LLM and
TTS overlap; record real first-audio latency including first-sentence wait.
Select an accepted performance profile before claiming natural/low-latency
conversation. No smart-home tool is required yet; barge-in remains disabled.

## Suggested commit

```text
feat: add multi-turn conversation sessions with the selected orchestrator
```

---

# PHASE 5 — Router, tool registry and Home Assistant smart-home fast path

## Goal

Support both:

1. low-latency simple commands;
2. open-ended conversation;

without sending every trivial command through a large LLM.

---

## 5.1 Tool model

Create:

```python
ToolDefinition:
    name
    description
    input_schema
    risk_level
    timeout_seconds
    allowed_targets
    retry_policy

ProposedAction:
    request_id
    session_id
    turn_id
    tool_name
    operation
    resolved_targets
    validated_arguments
    effective_risk
    expires_at

ToolResult:
    outcome                 # succeeded / failed / unknown / partial / rejected
    spoken_summary
    data
    error
```

Risk levels:

```text
READ_ONLY
REVERSIBLE
SENSITIVE
DESTRUCTIVE
```

Default policy:

- `READ_ONLY`: execute directly;
- `REVERSIBLE`: execute if explicitly requested;
- `SENSITIVE`: require confirmation;
- `DESTRUCTIVE`: disabled by voice until explicitly enabled, always confirm.

Compute effective risk from the resolved operation, targets and validated
arguments under local policy. Reject unclassified/out-of-allowlist actions.
The LLM may propose an action but cannot assign its risk or grant confirmation.
Bind a confirmation to that exact pending action and session, expire it, and
consume it once. Changed targets/arguments require a new check; ambiguous
speech must ask for clarification before any write API is called.

---

## 5.2 Router

Priority:

```text
1. conversation meta-command
   - stop
   - sleep
   - repeat
   - cancel
   - volume

2. high-confidence local/smart-home command

3. conversation backend / LLM
```

Do not build a giant regex-based Vietnamese NLU.

The local router should only handle commands that are safe and unambiguous.

---

## 5.3 Home Assistant adapter

Add:

```text
src/smart_hub/tools/home_assistant.py
```

Configuration:

```json
{
  "home_assistant": {
    "enabled": true,
    "base_url": "http://127.0.0.1:8123",
    "token_env": "HOME_ASSISTANT_TOKEN",
    "allowed_services": ["light.turn_on", "light.turn_off"],
    "allowed_entities": ["light.phong_khach"],
    "conversation_execution_enabled": false
  }
}
```

Never put the token in JSON. The entity above is illustrative; replace it with
one actual device explicitly selected for the future Phase 5 trial.

Start with state reads and a small allowlist of explicit service calls for
one approved device, for example `light.turn_on` / `light.turn_off`. Resolve
Vietnamese aliases to concrete entity ids locally, validate arguments and
authorize the action before dispatch. Extend domains only as requested.

**`conversation/process` is not a read-only intent parser or a dry-run API.**
It can execute an action before returning its response
([HA conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/)). Do not send arbitrary
transcripts there and attempt to confirm after the response. Keep generic HA
conversation execution disabled for the initial adapter. A later conversation
adapter needs an enforceable pre-dispatch boundary for the exact action and
an exposed-entity/intent allowlist; if that cannot be established, retain the
explicit service path.

Requirements:

- connection health check;
- timeout;
- exact entity/service/argument allowlists and pre-dispatch confirmation policy;
- distinguish successful, failed, partial and uncertain write outcomes;
- use observed device state where available to substantiate the spoken result;
- useful fallback when HA is down;
- never expose bearer token in logs;
- mock server tests.

Examples for incremental domain support; the initial light-only adapter must
clarify/reject unsupported writes instead of forwarding them elsewhere:

```text
"Bật đèn phòng khách."
"Tắt TV."
"Điều hòa đang bao nhiêu độ?"
```

Expected flow:

```text
STT
 ↓
router -> resolve exact action/target -> validate -> authorize/confirm
 ↓
HomeAssistantTool
 ↓
approved HA service call (or state read)
 ↓
ToolResult
 ↓
spoken response
```

### Timeout, cancellation and duplicate prevention

- Track a request id and dispatch state before issuing a write. Repeated
  callbacks for the same action/confirmation must not dispatch twice.
- Retry reads with bounded backoff if appropriate. A write timeout after
  dispatch means `unknown`, not "nothing happened".
- For `unknown`/`partial`, inspect read-only state where meaningful or say the
  result cannot be confirmed. Do not automatically retry, send the transcript
  through HA conversation, or let an LLM issue the same fallback action.
- Cancellation after dispatch cannot undo a device action; keep its outcome
  record even if the spoken answer is cancelled. An in-memory request id
  does not provide exactly-once execution across process restarts.
- If a device has no reliable state feedback (for example a one-way remote
  command), distinguish "command sent" from "device state confirmed".

---

## 5.4 Core local tools

Safe initial tools:

```text
system.status
system.uptime
system.cpu_temperature   # if available
system.disk_free
audio.get_volume
audio.set_volume
assistant.repeat
assistant.sleep
```

Do not add arbitrary `shell(command)`.

`audio.set_volume` initially targets playback only, within configured limits.
It must not alter microphone gain or routing as a side effect.

---

## Tests

- router priority;
- safe vs risky tool policy;
- no write API call before exact-action validation/required confirmation;
- ambiguous/out-of-allowlist targets cause no device call;
- expired/reused/changed-action confirmation is rejected;
- HA auth header not logged;
- post-dispatch timeout/partial result yields unknown/partial with no automatic duplicate write;
- a failed HA route cannot fall through to a second execution path;
- tool result becomes concise spoken answer;
- tool call cancellation;
- confirmation state machine.

## Acceptance

The same session can mix command and chat:

```text
User: Maika ơi
User: bật đèn phòng khách
Maika: Đã bật đèn phòng khách.
User: tiện thể giải thích giúp tôi MQTT là gì
Maika: ...
User: tắt đèn luôn
Maika: Đã tắt.
```

The approved device/service is resolved before dispatch. Mock tests prove
that denied/ambiguous/unconfirmed calls send zero writes and repeated events
send at most one write for a single in-process request. Exercise a lost response
after a successful device write; Maika reports uncertainty or verifies state
without repeating the action. No BroadLink adapter is implemented by this
phase unless separately requested.

## Suggested commit

```text
feat: add tool router and Home Assistant integration
```

---

# PHASE 6 — Acoustic echo cancellation and real barge-in

## Goal

Allow the user to interrupt Maika while Maika is speaking.

This is the phase that changes the experience from a conventional half-duplex smart speaker toward a desktop realtime voice experience.

---

## 6.1 Why current `feedback.py` cannot be used for barge-in

Current M1 logic deliberately does:

```text
assistant speaker active
       ↓
discard all microphone frames
       ↓
wait another ~0.7 s for echo decay
```

Keep this behaviour for M1.

For conversation mode, add a separate full-duplex path.

---

## 6.2 PipeWire echo cancellation

Prefer PipeWire's built-in echo-cancel module using WebRTC AEC.

Validate interactively first; do not silently write user configuration.

Inspect the installed PipeWire 0.3.65/WirePlumber 0.4.13 modules and their
supported options on this Debian machine. Current upstream examples may
target a newer release; do not copy them or upgrade the audio stack blindly.
Explain any proposed routing/configuration change before a coordinated test.

Create:

```bash
smart_hub.py check-aec
```

The command should report:

- current physical source;
- current physical sink;
- whether echo-cancel source exists;
- whether echo-cancel sink exists;
- selected devices for assistant capture/playback.

Target graph:

```text
Maika PCM -> echo-cancel sink -> playback -> physical speaker
                  │
                  └── digital playback reference ──┐
                                                  ▼
physical mic -----------------------------------> AEC
                                                  │
                                           cleaned source
                                                  │
                                                  ▼
                                              AudioPump
```

All Maika TTS playback must go through the AEC sink when full-duplex is enabled.

AudioPump must capture from the echo-cancel source.

The cancellation reference is the digital PCM sent through that sink, not
an independently measured signal from the physical speaker. A TV or another
person outside that reference remains speech to VAD. AEC and a VAD threshold
do not identify the intended speaker or guarantee rejection of TV dialogue.

---

## 6.3 Interruption logic

While `SPEAKING`:

1. VAD continues processing microphone audio.
2. When genuine user speech starts:
   - invalidate the active response generation id;
   - emit `Interrupted`;
   - cancel active LLM stream;
   - clear pending sentence/TTS queue;
   - terminate current playback;
   - remove unspoken assistant text from spoken-context bookkeeping;
   - transition to `LISTENING`.
3. The user's new speech becomes the next turn.

Retain clean preroll for that new turn. Late provider completions from the
cancelled generation cannot enqueue text/audio or mutate its successor.

Target metric:

```text
barge_in_latency_ms =
time(playback_stopped) - time(user_speech_started)
```

Initial goal:

```text
P50 < 250 ms
P90 < 400 ms
```

Treat this as a benchmark target, not a guarantee. Distinguish VAD event time
from actual user speech onset, and a `stop()` call from sound ceasing at the
speaker. Record the measurement method and device buffering; if only software
timing is available, label it as such and leave acoustic latency unverified.

---

## 6.4 False interruption protection

Separate the following cases instead of treating them as one noise test:

| Case | Initial pilot gate |
| --- | --- |
| Maika's own speech only | At least 10 min of representative playback, user silent; zero false cancellations |
| Independent TV/background speech | At least 10 min, user silent; record setup and false cancellations separately; zero is the initial target for that tested profile |
| Real interruption | At least 20 new trials at the intended distance/volume; at least 18/20 interrupt and retain the new request, with P50/P90 latency recorded |
| Impulse/fan noise | Record attempted noises and false cancellations; no cancellation from the tested non-speech impulses |
| Lifecycle | No new-session wake during a session; cancelled audio never resumes; fallback mode still completes turns |

These are screening targets, not guarantees in every room. If TV speech
causes false interruption, measure the limitation before tuning. Speaker
placement, microphone direction or an explicit interruption gesture can be
evaluated as separate options; speaker identification/source separation is
not silently added to this phase. Do not raise thresholds until the intended
user can no longer interrupt and then call noise rejection successful.

Provide a config fallback:

```json
{
  "conversation": {
    "barge_in_enabled": false
  }
}
```

If AEC is unavailable, remain half-duplex rather than pretending full-duplex works.

If the pilot gate fails, keep `barge_in_enabled: false` for that operating
profile and record AEC/barge-in as failed or experimental. A documented
half-duplex fallback may ship, but is not a PASS for full-duplex capability.

---

## Acceptance

During a long response:

> Maika: “Có ba cách để làm việc này. Cách thứ nhất…”  
> User: “Khoan, nói cách thứ ba thôi.”  
> Maika stops quickly, recognizes the new request, and answers it.

The pilot matrix and measured latency must also pass for any profile enabling
barge-in by default. Hardware permissions/readiness are required for live
tests; unperformed tests remain NOT TESTED.

## Suggested commit

```text
feat: add PipeWire AEC and full-duplex voice interruption
```

---

# PHASE 7 — Codex CLI, MCP and application integrations

## Goal

Let Maika become a voice front-end to other software without giving arbitrary voice input unrestricted shell access.

---

## 7.1 Start read-only

Initial Codex tools:

```text
codex.status
codex.list_sessions
codex.current_task
```

Then add controlled write actions:

```text
codex.submit_task
codex.send_followup
codex.cancel_task
```

Do not expose:

```text
shell("<arbitrary transcript>")
```

---

## 7.2 Codex adapter

Implement a provider boundary:

```python
class AgentToolProvider(Protocol):
    async def status(...)
    async def submit_task(...)
    async def cancel(...)
```

If Codex CLI has a stable machine-readable mode, prefer it.

If not, use a small wrapper and strict structured parsing.

Never parse coloured interactive terminal output if a structured interface exists.

---

## 7.3 MCP client

Add MCP only after the internal `ToolRegistry` is stable.

Design:

```text
ToolRegistry
 ├── internal tools
 ├── Home Assistant adapter
 └── MCP adapter
       ├── server A tools
       ├── server B tools
       └── ...
```

MCP is an integration layer, not the core assistant state machine.

Requirements:

- allowlist servers;
- allowlist tools per server;
- tool timeout;
- risk mapping;
- argument validation;
- confirmation before sensitive actions;
- redact credentials from logs;
- distinguish tool transport failure from tool execution failure.

---

## 7.4 Voice-specific UX for long jobs

Do not keep the user waiting silently while Codex runs for minutes.

Example:

```text
User:
"Bảo Codex kiểm tra lỗi build project X."

Maika:
"Được, em đã giao Codex kiểm tra. Khi có kết quả em sẽ báo."
```

Long-running jobs become asynchronous tasks.

Store only task metadata:

```text
task_id
provider
summary
status
created_at
updated_at
result_summary
```

Do not keep the voice conversation blocked.

A later notification can be spoken only when appropriate, or surfaced through another application.

---

## Tests

- read-only tool invocation;
- risk policy;
- destructive confirmation;
- task timeout;
- malformed external response;
- MCP server unavailable;
- Codex unavailable;
- cancellation;
- no transcript used as raw shell.

## Suggested commit

```text
feat: expose Codex and MCP integrations through safe tool registry
```

---

# PHASE 8 — 24/7 service, resilience and observability

## Goal

Turn the prototype into a reliable Debian service.

---

## 8.1 systemd user service

Use a **user service**, not root, because the application needs the user's PipeWire/WirePlumber audio session.

Add:

```text
deploy/systemd/smart-hub.service
```

Conceptual unit:

```ini
[Unit]
Description=Smart Hub Maika Voice Assistant
After=pipewire.service wireplumber.service
Wants=pipewire.service wireplumber.service

[Service]
Type=simple
WorkingDirectory=%h/source/wake-work/smart-hub
ExecStart=%h/source/wake-work/smart-hub/.venv/bin/python \
  %h/source/wake-work/smart-hub/scripts/smart_hub.py assistant
Restart=on-failure
RestartSec=3
EnvironmentFile=-%h/.config/smart-hub/env

[Install]
WantedBy=default.target
```

Codex must adapt paths for the actual machine rather than blindly copying them.

The env file is optional for a fully local profile. If an enabled provider
requires a credential, configuration validation must report its absence.

Do not enable lingering automatically.

Document the choice separately because keeping the service alive without an interactive login may require user-manager/lingering validation on the target Debian system.

---

## 8.2 Recovery

Add bounded recovery for:

- `arecord` exits;
- playback process failure;
- temporary PipeWire restart;
- LLM backend timeout;
- Home Assistant unavailable;
- STT/TTS provider exception.

Example:

```text
audio failure
   ↓
state ERROR
   ↓
close stale processes
   ↓
1 s / 2 s / 5 s bounded retry
   ↓
reopen capture
   ↓
SLEEPING
```

Do not retry forever at high frequency.

---

## 8.3 Health and diagnostics

Add:

```bash
smart_hub.py status
smart_hub.py doctor
```

`doctor` should check:

- PipeWire available;
- capture device;
- playback device;
- wake model;
- wake references;
- STT model;
- TTS model;
- optional AEC nodes;
- Home Assistant connectivity if enabled;
- LLM backend if enabled;
- disk free;
- dependency versions.

Do not make `doctor` modify the system.

---

## 8.4 Structured logs

Human-readable journal output by default.

Optional JSON lines in diagnostic mode.

Track:

```text
wake_count
wake_score
false/rejected clipping count
session_count
turn_count
user_turn_seconds
stt_latency_ms
llm_ttft_ms
llm_total_ms
tts_first_audio_ms
tts_total_ms
barge_in_latency_ms
tool_latency_ms
audio_restart_count
provider_error_count
```

Do not log:

- auth tokens;
- full environment;
- raw audio;
- full transcript by default.

Allow transcript logging only through an explicit privacy/debug setting.

---

## 8.5 Single-instance protection

Prevent two assistant processes from opening the same audio pipeline simultaneously.

Use a user-runtime lock/pid file or systemd ownership.

---

## Acceptance

- reboot test;
- user service starts in the intended user audio session;
- assistant survives at least 8 hours;
- temporary network loss does not kill wake-word listening;
- temporary Home Assistant failure does not kill assistant;
- graceful SIGTERM leaves no `arecord`/`aplay` zombie process.

## Suggested commit

```text
ops: run Maika as resilient systemd user service
```

---

# PHASE 9 — Hardening and release acceptance

## Goal

Validate that the system actually works in the living room, not only in unit tests.

---

## 9.1 Wake-word soak tests

Extend the accepted Phase 0 pilot; this is not the first repeated-wake/noise
validation. Recheck repeated calls after long idle and audio recovery, and
rerun relevant pilot conditions whenever gain, routing or detector input changes.

Test scenarios:

- quiet room;
- fan;
- TV;
- music;
- people speaking;
- Maika's own speaker;
- 1 m / 2 m / 3 m practical distances;
- several hours.

Record only event/score metrics unless user explicitly enables audio recording.

Track:

```text
true positives
false positives
duplicate wakes
missed wakes
```

Do not claim a false-positive rate from a tiny sample.

---

## 9.2 Conversation latency benchmark

At least 20 real Vietnamese turns.

Measure:

```text
end of user speech
 → final STT
 → first backend token
 → first TTS PCM
 → first speaker audio
```

Report P50/P90 and compare with the Phase 3/4 accepted performance profile,
using the same timing definitions and Vietnamese quality checks. Include
concurrent local LLM/TTS load, audio gaps, CPU/RSS and cold-start observations.

Optimize the largest contributor first.

---

## 9.3 Interruption benchmark

At least 20 interruptions while TTS is playing.

Measure:

```text
user speech start -> playback stop
```

Also measure false interruption count when the user remains silent.

---

## 9.4 Failure injection

Test:

- unplug/disable mic;
- kill `arecord`;
- kill playback;
- stop/restart PipeWire;
- disconnect network;
- stop Home Assistant;
- make LLM backend return 500/timeout;
- missing STT/TTS model;
- disk nearly full;
- invalid config.

Runtime must fail safely.

---

## 9.5 Security review

Check:

- no root runtime;
- no world-readable tokens;
- no arbitrary shell tool;
- no external network listener by default;
- destructive voice actions require confirmation;
- no secret values in diagnostics;
- no model/runtime auto-download;
- no raw audio saved by default;
- MCP servers explicitly allowed;
- external tool output treated as untrusted text.

---

## Release acceptance

Call the first complete release:

```text
Maika Voice v1
```

only when all are true:

- [ ] M1 repeated-wake/noise pilot and user stability confirmation are recorded; regression tests remain green.
- [ ] Phase 1B ownership/dependency decision is recorded and only one turn controller is active.
- [ ] Multi-turn conversation works.
- [ ] Dynamic Vietnamese TTS works.
- [ ] Local STT/TTS and the real conversation backend meet recorded Vietnamese quality and performance targets, or an explicitly accepted slower profile.
- [ ] Session timeout works.
- [ ] Home Assistant simple commands work if HA is enabled.
- [ ] Tool permissions/confirmation run before dispatch; uncertain write outcomes cannot cause automatic duplicate execution.
- [ ] AEC/barge-in passes its separate self-echo/TV/interruption checks OR stays disabled with a documented half-duplex profile; a disabled feature is not marked PASS.
- [ ] 24/7 service survives soak test.
- [ ] Recovery tests pass.
- [ ] No secrets/audio leakage.
- [ ] README has clean install and troubleshooting steps.

---

# 6. Dependency strategy

Do not put all future packages into the existing `requirements.txt` at once.

Recommended split:

```text
requirements.txt
    current wake-word core

requirements-conversation.txt
    selected VAD / turn dependencies only
    faster-whisper (Phase 3 onward)
    Pipecat only if selected in Phase 1B
    HTTP client as needed

requirements-voice.txt
    VieNeu generation/runtime dependencies

requirements-dev.txt
    test/lint tooling
```

Alternative: migrate to `pyproject.toml` with optional dependency groups only after Phase 3 if Codex can do so without destabilizing `scripts/setup_env.py`.

If migrating:

```text
smart-hub[wake]
smart-hub[conversation]
smart-hub[vieneu]
smart-hub[dev]
```

Do not combine packaging migration and conversation runtime in one commit.

Pin the exact versions that are tested on Debian 12/Python 3.11.

Before each installation, explain the package purpose, why existing packages
are insufficient, the transitive/download footprint and a lighter alternative.
Do not install future extras in advance. Keep the completed Phase 1B experiment isolated;
use the core pins as constraints when resolving additions and report conflicts
instead of silently upgrading the M1 environment. Test missing-model failure
with outbound network blocked; a library's local inference mode may still
download assets during initialization unless explicitly prevented.

---

# 7. Configuration evolution

Keep old flat keys valid.

Add optional nested sections.

Example future configuration after the relevant phases are implemented; do
not copy these sections into today's M1 config loader, which rejects unknown
keys. Select the orchestration provider only after the Phase 1B decision:

```json
{
  "wake_word": "Maika ơi",
  "engine": "neural",
  "model_path": "models/maika_oi_neural.npz",
  "device": "pipewire",
  "feedback_enabled": true,
  "feedback_path": "assets/em_nghe_vieneu.wav",
  "playback_device": "pipewire",
  "threshold": 0.72,
  "cooldown_seconds": 2.0,
  "min_rms": 180,
  "silence_seconds": 0.4,
  "min_speech_seconds": 0.35,
  "max_speech_seconds": 2.5,

  "conversation": {
    "enabled": true,
    "language": "vi",
    "followup_timeout_seconds": 25,
    "max_turn_seconds": 30,
    "no_speech_timeout_seconds": 10,
    "preroll_ms": 320,
    "min_speech_ms": 250,
    "end_silence_ms": 700,
    "echo_tail_ms": 700,
    "capture_queue_max_ms": 500,
    "max_turns": 20,
    "max_session_seconds": 600,
    "barge_in_enabled": false
  },

  "stt": {
    "provider": "whisper",
    "model": "small",
    "model_path": "models/stt/selected-model",
    "device": "cpu",
    "compute_type": "int8"
  },

  "tts": {
    "provider": "vieneu",
    "voice": "Trúc Ly"
  },

  "llm": {
    "provider": "ollama",
    "model": ""
  },

  "home_assistant": {
    "enabled": false,
    "base_url": "http://127.0.0.1:8123",
    "token_env": "HOME_ASSISTANT_TOKEN",
    "allowed_services": [],
    "allowed_entities": [],
    "conversation_execution_enabled": false
  },

  "aec": {
    "enabled": false,
    "capture_device": "",
    "playback_device": ""
  }
}
```

Rules:

- unknown config keys should fail loudly;
- secrets only by env name;
- paths resolve relative to config file as existing code does;
- preserve backwards compatibility with M1 configs.

`stt.model` is a label for the benchmark-selected model; load its verified local
`model_path`, not a remote size/name. The illustrative `small` value is not a
performance selection. The LLM model is intentionally unset until Phase 4
benchmarking. Validate the active mode's required sections, and keep new
provider imports/model loads out of `listen` and dependency-free mock paths.
Empty tool allowlists deny all device actions.

---

# 8. Testing pyramid

## Unit

No microphone, no network:

```text
state
router
turn buffer
sentence buffer
tool policy
configuration
provider adapters with fakes
cancellation
```

## Integration

Still automated:

```text
WAV -> wake
PCM fixture -> VAD -> STT mock/real model
mock LLM -> TTS mock
mock HA HTTP/WebSocket
mock MCP server
```

## Hardware acceptance

Manual/interactive:

```text
real mic
real speaker
wake word
Vietnamese STT
Vietnamese TTS
AEC
barge-in
room noise
```

Do not make CI depend on microphone hardware.

---

# 9. Codex working protocol

Give Codex these instructions before each phase.

## Required workflow

1. Read:
   - `README.md`
   - `docs/milestone-1.md`
   - `docs/SMART_HUB_PLAN.md`
   - relevant source/tests.
2. For implementation phases, run baseline tests before editing. For a
   documentation-only revision, validate structure/references and changed
   commands; do not start hardware tests or a phase implicitly.
3. Implement only the current phase.
4. Add tests before/with implementation.
5. Run full old + new test suite.
6. Update README/docs for only the new behaviour.
7. Show:
   - files changed;
   - architecture decision;
   - commands run;
   - exact test results;
   - remaining limitations.
8. Stop at the phase acceptance boundary.
9. Do not proceed to the next phase unless instructed.

Before Phase 1, verify Phase 0 evidence and the user's M1 stability confirmation.
Before Phase 2, verify the Phase 1B orchestration decision. Proposed commands,
acceptance targets and unchecked boxes in this roadmap are not completed work.
Explain dependencies before installing and coordinate live speaking windows.
Mark missing hardware/user observations as NOT TESTED, not PASS.

Before starting/configuring any future service, inspect listening ports and
verify the intended port is free or belongs to that service. Bind internal
endpoints to loopback and check the owning process/health after startup; do
not stop another service to claim its port.

## Things Codex must not do without explicit approval

- change PipeWire/WirePlumber persistent system configuration;
- run commands with `sudo`;
- enable systemd lingering;
- modify microphone/speaker volume automatically;
- overwrite wake enrollment;
- record or upload microphone audio;
- add a cloud dependency as mandatory;
- expose an HTTP/WebSocket port externally;
- execute arbitrary shell from recognized speech;
- store API tokens in the repository;
- delete current M1 paths/tests to make the new architecture simpler.

---

# 10. Copy/paste prompts for Codex CLI

## Start Phase 0

```text
Read README.md, docs/milestone-1.md and docs/SMART_HUB_PLAN.md in full.
Execute PHASE 0 only.

Do not implement any later phase.
Run the existing tests first, then arrange the repeated-wake, noise and negative
pilot checks with the user. Distinguish a missed second wake from expected M1
behaviour after the fixed reply. Count actual calls, events and audible replies.
If a defect is reproduced, make only a focused M1 fix with regression evidence.
Do not tune thresholds without diagnostic evidence or overwrite enrollment.
Explain any proposed system/audio change before doing it; add no dependencies,
STT, LLM, device control or service. Record NOT TESTED for incomplete live checks.
At the end report files changed, commands run, exact test results and any mismatch
between the repository and the plan. M1 acceptance requires the user's explicit
stability confirmation. Stop within Phase 0; do not start a later phase.
```

## Start Phase 1

```text
Read docs/SMART_HUB_PLAN.md and implement PHASE 1 only after the M1 acceptance gate.

Preserve the current `listen` command and all existing M1 tests.
Add the minimal assistant state machine/events, a long-lived AudioPump,
and `assistant --mock`.
The production assistant path must use one long-lived capture source; do not
reopen the microphone on every state transition.
Implement a thread-safe bounded handoff, explicit audio gaps/overflow policy,
half-duplex playback suppression and generation-based rejection of stale work.
Do not add STT, LLM, Pipecat or dynamic TTS yet.

Add tests, run the entire suite, document the new command, then stop.
```

## Start Phase 1B

```text
Read docs/SMART_HUB_PLAN.md and execute PHASE 1B only.

Evaluate a minimal local Pipecat bridge against the lightweight local turn
controller before Phase 2. Explain dependency/footprint choices before installing
anything and use an isolated ignored environment that preserves M1 core pins.
Use synthetic/approved local PCM and fake STT/LLM/TTS; no real conversation,
microphone recording, device control, cloud transport or service.
Test turn events, suppression, bounded handoff, cancellation and clean shutdown.
Record measurements and one ownership decision in
docs/decisions/conversation-orchestration.md. Pipecat may be deferred.
Do not implement Phase 2. Stop after the decision checkpoint.
```

## Start Phase 2

```text
Implement PHASE 2 only from docs/SMART_HUB_PLAN.md, using the Phase 1B decision.

Add the selected conversation VAD/turn owner behind an abstraction; no duplicate
turn controller. Adapt 20 ms capture frames to the pinned VAD input contract.
Do not repurpose or break the existing M1 SpeechSegmenter.
Add capture-only assistant mode and tests for preroll, turn end, timeout,
bounded buffers, gap invalidation, echo suppression and no recording by default.
Do not add a real STT or LLM yet.

Run all old and new tests and stop after Phase 2 acceptance.
```

## Start Phase 3

```text
Implement PHASE 3 only from docs/SMART_HUB_PLAN.md.

Add local faster-whisper behind STTProvider, an explicit model setup/download
step, refactor the existing VieNeu Nano implementation into a reusable TTS
provider, add cancellable PCM playback, and complete a deterministic one-turn
voice loop.

Do not add a real LLM or Home Assistant yet.
Do not allow runtime model downloads.
Work through STT benchmarking, TTS benchmarking and then the one-turn loop.
Test the 4.5 s implementation guard using bounded subdivision and long Vietnamese
text, names and numbers. Do not confuse chunk output with incremental synthesis.
Apply the plan's 20-utterance quality/CPU/latency gate, report cold and warm
P50/P90 with speech-end-to-speaker timing, and reject stale generation results.
If targets fail, leave performance acceptance pending unless the user explicitly
accepts a measured slower profile.
Run the complete suite and stop.
```

## Start Phase 4

```text
Implement PHASE 4 only from docs/SMART_HUB_PLAN.md.

Add in-memory multi-turn ConversationSession, a provider-neutral streaming
conversation backend and extend the orchestrator selected in Phase 1B.
Do not add a new turn owner or migrate frameworks in this phase.
Do not require Daily, LiveKit or any cloud transport for this single Debian host.
Use mock backend in automated tests.

Implement follow-up listening and session timeout.
Start timeout after playback/echo guard. Bound context/output and track completed
vs partial/unspoken chunks. Implement one measured real backend and validate
Vietnamese quality/latency with local LLM and TTS overlap; cloud text use is opt-in.
Do not add Home Assistant/tools yet.
Run all tests and stop.
```

## Start Phase 5

```text
Implement PHASE 5 only from docs/SMART_HUB_PLAN.md.

Add ToolRegistry, risk levels, command/conversation routing, safe local tools,
and optional Home Assistant integration with its token read only from an
environment variable.
Do not expose arbitrary shell.
Add confirmation flow for sensitive actions.
Resolve and authorize the exact target/operation/arguments before any write API.
Start HA with allowlisted state reads and explicit services for one device;
keep generic conversation/process execution disabled because it can act directly.
Bind confirmation to the pending action. Test duplicate callbacks, partial results
and a write that succeeds but loses its response: no automatic write retry/fallback.
Use mocks for automated HA tests.
Run all tests and stop.
```

## Start Phase 6

```text
Implement PHASE 6 only from docs/SMART_HUB_PLAN.md.

Do not change M1 feedback suppression semantics.
Add a separate conversation full-duplex path using PipeWire echo cancellation.
First provide diagnostic/check tooling; do not silently persist PipeWire or
WirePlumber configuration.
When AEC is verified, enable VAD while TTS plays and implement cancellation of
LLM/TTS/playback on real user speech.
Keep a configuration fallback with barge-in disabled.
Verify options against the installed PipeWire version. Separately test Maika
self-echo and independent TV speech; AEC does not identify the intended speaker.
Invalidate cancelled generations so old playback cannot resume. Keep barge-in
disabled for profiles that fail the pilot gate, and distinguish software timing
from measured acoustic interruption latency.

Measure interruption latency on hardware, run tests, document results and stop.
```

## Start Phase 7

```text
Implement PHASE 7 only from docs/SMART_HUB_PLAN.md.

Integrate Codex and MCP through ToolRegistry.
Start with read-only/status actions.
Never translate voice transcripts into arbitrary shell commands.
Add allowlists, timeouts, risk mapping and confirmation for side effects.
Long-running Codex tasks must be asynchronous from the voice turn.

Use mocks for all automated external-agent tests.
Run the full test suite and stop.
```

## Start Phase 8

```text
Implement PHASE 8 only from docs/SMART_HUB_PLAN.md.

Add systemd user-service files, diagnostics, bounded runtime recovery,
single-instance protection and structured metrics/logging.
Do not use root.
Do not automatically enable lingering or modify persistent audio configuration.
Do not log raw audio, tokens or full transcripts by default.

Run restart/failure tests that can be automated, document required manual
reboot/audio tests, and stop.
```

---

# 11. Framework choices

## Candidate: Pipecat, decided in Phase 1B

If the early spike selects it, use it primarily for:

- VAD/turn lifecycle;
- interruption semantics;
- streaming conversation pipeline;
- STT/TTS service integration where useful.

Do not rewrite the wake detector around it. If the local controller is chosen,
Pipecat remains deferred and the later conversation milestones are still valid.
Pin the tested release; upstream VAD/turn APIs and defaults must be checked
against that release, including any model downloads and English STT defaults.

Relevant upstream documentation reviewed while preparing this plan:

- Pipecat speech input / turn detection:
  `https://docs.pipecat.ai/pipecat/learn/speech-input`
- Pipecat local Whisper:
  `https://docs.pipecat.ai/api-reference/server/services/stt/whisper`
- Pipecat Piper TTS:
  `https://docs.pipecat.ai/api-reference/server/services/tts/piper`

Pipecat currently documents local Silero VAD, interruption support, Faster-Whisper and local/self-hosted Piper integration.

Supporting contracts checked during review:

- [Python asyncio queues are not thread-safe](https://docs.python.org/3.11/library/asyncio-queue.html).
- [Silero ONNX wrapper input sizes](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py): validate the pinned model's 16 kHz/512-sample contract before bridging 20 ms frames.
- [faster-whisper CPU INT8, generator timing and local model loading](https://github.com/SYSTRAN/faster-whisper).

## Home Assistant

Use as smart-home integration rather than conversation-runtime owner.

The conversation API can perform actions; the Phase 5 initial adapter therefore
uses explicit allowlisted services. Its response is not a pre-execution preview.

Relevant APIs:

- Assist pipelines:
  `https://developers.home-assistant.io/docs/voice/pipelines/`
- Conversation API:
  `https://developers.home-assistant.io/docs/intent_conversation_api/`
- Explicit service calls and state reads:
  `https://developers.home-assistant.io/docs/api/rest/`
- Assist satellite:
  `https://developers.home-assistant.io/docs/core/entity/assist-satellite/`

## PipeWire AEC

Use for full-duplex speaker/microphone echo cancellation:

- `https://pipewire.pages.freedesktop.org/pipewire/page_module_echo_cancel.html`

The upstream module exposes echo-cancel capture/source and playback/sink nodes and supports the WebRTC AEC implementation.

Cancellation depends on a playback reference. Independent TV speech needs
separate validation, and the installed Debian version must support the chosen
module options before any configuration is proposed.

## Alternatives not selected as the initial architecture

### TEN Framework

Good realtime voice framework and worth reevaluating if Pipecat cannot be bridged cleanly to the local audio stack, but adopting it now would cause a larger architectural rewrite.

### LiveKit Agents

Useful when Maika needs remote/mobile/multi-room WebRTC clients. It is unnecessary as a mandatory dependency for the first single-machine living-room assistant.

---

# 12. Milestone summary

```text
M1  IMPLEMENTED / USER ACCEPTED STT WAKE FOR CURRENT USE
    Quantified multi-speaker/noise/soak evidence remains follow-up work

M2  Phase 1 DONE -> Phase 1B DONE (LOCAL) -> Phase 2 SOFTWARE DONE
    Runtime primitives -> one orchestration owner -> conversational turn capture
    Phase 2 real-speaker acceptance pending; paused at user's request

M3  Phase 3
    CPU/quality benchmarks -> local STT + dynamic Vietnamese TTS -> one-turn loop

M4  Phase 4
    Multi-turn conversation with the selected owner and a measured real backend

M5  Phase 5
    Exact-action authorization before dispatch; no duplicate writes on uncertainty

M6  Phase 6
    Optional AEC/barge-in, gated by self-echo/TV/real-interruption trials

M7  Phase 7
    Codex/MCP/application integrations

M8  Phase 8–9
    24/7 service + resilience + production validation
```

---

# 13. Definition of “feels like desktop voice”

Do not consider the project complete merely because it can do:

```text
wake -> record -> answer -> sleep
```

The target UX requires:

- one wake phrase to start a session;
- natural follow-up turns;
- semantic context across turns;
- low dead-air latency;
- speech begins before the full response is generated;
- assistant can be interrupted after AEC is enabled;
- current spoken vs unspoken response is tracked correctly;
- tool calls are integrated into the same conversation;
- errors are spoken concisely rather than crashing;
- user can explicitly say `dừng`, `thôi`, `đi ngủ`;
- assistant returns to lightweight wake-only idle mode after timeout.

That is the acceptance definition for the conversational part of Maika.
