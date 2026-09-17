"""Explicit lifecycle for wake, fixed acknowledgement and one command transcript."""
from enum import Enum

from .events import AssistantStateChanged


class AssistantState(str, Enum):
    SLEEPING = "sleeping"
    ACKNOWLEDGING = "acknowledging"
    LISTENING_COMMAND = "listening_command"
    LISTENING_TURN = "listening_turn"
    SAVING_TURN = "saving_turn"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


TRANSITIONS = {
    AssistantState.SLEEPING: {AssistantState.ACKNOWLEDGING, AssistantState.STOPPING, AssistantState.FAILED},
    AssistantState.ACKNOWLEDGING: {AssistantState.SLEEPING, AssistantState.LISTENING_COMMAND, AssistantState.LISTENING_TURN,
                                  AssistantState.STOPPING, AssistantState.FAILED},
    AssistantState.LISTENING_COMMAND: {AssistantState.SLEEPING, AssistantState.STOPPING, AssistantState.FAILED},
    AssistantState.LISTENING_TURN: {AssistantState.SLEEPING, AssistantState.SAVING_TURN,
                                    AssistantState.STOPPING, AssistantState.FAILED},
    AssistantState.SAVING_TURN: {AssistantState.SLEEPING, AssistantState.STOPPING, AssistantState.FAILED},
    AssistantState.FAILED: {AssistantState.STOPPING},
    AssistantState.STOPPING: {AssistantState.STOPPED},
    AssistantState.STOPPED: set(),
}


class StateMachine:
    def __init__(self, emit=lambda event: None):
        self.state = AssistantState.SLEEPING
        self.emit = emit

    def transition(self, target, context):
        target = AssistantState(target)
        if target not in TRANSITIONS[self.state]:
            raise ValueError(f"Chuyển trạng thái không hợp lệ: {self.state.value} -> {target.value}.")
        previous, self.state = self.state, target
        self.emit(AssistantStateChanged(context, previous.value, target.value))
