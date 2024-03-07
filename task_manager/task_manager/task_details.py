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
from typing import Any, Optional

# Task Manager messages
from task_manager_msgs.msg import TaskStatus


@dataclass
class TaskDetails:
    """Helper dataclass holding the more detailed public information about a single task.

    Params:

        task_id -- Unique UUID of the task

        source -- Where the task was initiated from. For example "CLOUD"

        status -- Current status of the task in str(TaskStatus)

        result -- Final result of the task
    """

    # Constructor arguments
    task_id: str
    source: str
    status: TaskStatus

    # Other public fields (not constructor args)
    result: Optional[Any] = field(default=None, init=False)
