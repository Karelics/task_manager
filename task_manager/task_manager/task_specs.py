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

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List


class TaskServerType(Enum):
    """Server type of the task."""

    SERVICE = "service"
    ACTION = "action"


@dataclass
class TaskSpecs:  # pylint: disable=too-many-instance-attributes
    """Task specifications."""

    task_name: str
    topic: str
    msg_interface: Any
    task_server_type: TaskServerType
    blocking: bool = False
    cancel_on_stop: bool = False
    cancel_reported_as_success: bool = False
    reentrant: bool = False
    service_success_field: str = ""
    cancel_timeout: float = 5.0
    require_finish_on_parallel_cancel: bool = True
    # Action-result sequence/string fields whose partial values, collected each time the task is paused, are
    # concatenated (in chronological order) into the final result when the task eventually finishes. Fields not
    # listed here keep only the final segment's value. Only meaningful for action-backed tasks.
    result_concat_fields: List[str] = field(default_factory=list)
