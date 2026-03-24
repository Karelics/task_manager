#!/usr/bin/env python3

#  ------------------------------------------------------------------
#   Copyright 2026, Frantisek Nekovar
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

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from task_manager_msgs.msg import ActiveTask


@dataclass
class RobotStatus:
    """Tracks the status of a single robot in the multi-robot system."""

    robot_id: str  # Robot prefix, e.g., "uav1", "uav2"
    active_tasks: List[ActiveTask] = field(default_factory=list)
    last_seen: datetime = field(default_factory=datetime.now)
    is_connected: bool = False
    total_tasks_assigned: int = 0

    def is_available(self) -> bool:
        """Check if robot is available to accept new tasks (has no active tasks and is connected)."""
        return self.is_connected and len(self.active_tasks) == 0

    def update_active_tasks(self, tasks: List[ActiveTask]) -> None:
        """Update the list of active tasks and connection status."""
        self.active_tasks = tasks
        self.last_seen = datetime.now()
        self.is_connected = True

    def mark_disconnected(self) -> None:
        """Mark robot as disconnected."""
        self.is_connected = False
        self.active_tasks = []
