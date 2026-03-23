#!/usr/bin/env python3

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

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TaskAssignment:
    """Tracks the assignment of a task to a robot."""

    task_id: str
    task_name: str
    robot_id: str
    assigned_at: datetime
    status: str  # "PENDING", "SENT", "IN_PROGRESS", "DONE", "ERROR", "CANCELED"
    task_data: str  # JSON formatted task data
    source: str  # Source of the task request

    def __post_init__(self):
        if isinstance(self.assigned_at, str):
            self.assigned_at = datetime.fromisoformat(self.assigned_at)
