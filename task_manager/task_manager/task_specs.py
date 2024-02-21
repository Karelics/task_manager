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
from enum import Enum
from typing import Any


class TaskServerType(Enum):
    """Server type of the task."""

    SERVICE = "service"
    ACTION = "action"


@dataclass
class TaskSpecs:
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
