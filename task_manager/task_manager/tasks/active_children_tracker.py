#  ------------------------------------------------------------------
#   Copyright 2024 Karelics Oy
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#  ------------------------------------------------------------------
import threading
from abc import ABC, abstractmethod
from typing import Dict, List


class ActiveChildrenTracker(ABC):
    """Shared base for composite tasks (Mission, ParallelTaskExecutor, ...) whose pause/resume must redirect to
    whichever of their own children are currently active.

    Explicit subclassing also lets this class double as a mixin: get_active_children() is genuinely different per
    composite type and stays abstract, but the paused flag bookkeeping (request_pause/request_resume/is_paused, and the
    _resume_events dict backing them) is identical for every composite, so it's implemented once here instead of
    duplicated in each subclass. Subclasses must chain into this class' `__init__` via `super().__init__()`.
    """

    def __init__(self) -> None:
        # goal_id (bytes) of each running invocation of this composite -> Event; set = running, cleared = paused.
        self._resume_events: Dict[bytes, threading.Event] = {}

    @abstractmethod
    def get_active_children(self, goal_id: bytes) -> List[str]:
        """Returns the task_ids of this composite invocation's currently active (not yet finished) children."""

    def _start_pause_tracking(self, goal_id: bytes) -> None:
        """Registers goal_id as a running invocation whose paused flag can be armed/queried.

        Call once at the start of the composite's own execute callback, before dispatching anything.
        """
        self._resume_events[goal_id] = threading.Event()
        self._resume_events[goal_id].set()

    def _stop_pause_tracking(self, goal_id: bytes) -> None:
        """Reverses _start_pause_tracking() - call once the composite's own execute callback is about to return,
        in a `finally` block."""
        self._resume_events.pop(goal_id, None)

    def request_pause(self, goal_id: bytes) -> bool:
        """Arms this composite invocation's own paused flag, ahead of/independent from whatever happens to its currently
        active children.

        :return: False (no-op) if goal_id isn't a currently running invocation of this composite.
        """
        event = self._resume_events.get(goal_id)
        if event is None:
            return False
        event.clear()
        return True

    def request_resume(self, goal_id: bytes) -> bool:
        """Reverses request_pause().

        :return: False (no-op) if goal_id isn't a currently running invocation of this composite.
        """
        event = self._resume_events.get(goal_id)
        if event is None:
            return False
        event.set()
        return True

    def is_paused(self, goal_id: bytes) -> bool:
        """Whether this composite invocation is currently considered paused - consulted by
        _sync_composite_statuses to decide the composite's own displayed status, taking priority over its
        children's derived statuses."""
        event = self._resume_events.get(goal_id)
        return event is not None and not event.is_set()
