"""Operator commands. They control pacing and the run state, never world physics."""

from dataclasses import dataclass

MIN_SPEED = 1
MAX_SPEED = 1000  # ticks per second; the UI offers a smaller set.


class CommandError(Exception):
    """A command that is invalid in the current state; the world is unchanged."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True, slots=True)
class Start:
    command_id: str
    until_tick: int | None = None


@dataclass(frozen=True, slots=True)
class Resume:
    command_id: str
    until_tick: int | None = None


@dataclass(frozen=True, slots=True)
class Pause:
    command_id: str


@dataclass(frozen=True, slots=True)
class Step:
    command_id: str


@dataclass(frozen=True, slots=True)
class SetSpeed:
    command_id: str
    ticks_per_second: int


Command = Start | Resume | Pause | Step | SetSpeed
