#  ------------------------------------------------------------------
#   Copyright (C) Karelics Oy - All Rights Reserved
#   Unauthorized copying of this file, via any medium is strictly
#   prohibited. All information contained herein is, and remains
#   the property of Karelics Oy.
#  ------------------------------------------------------------------

from dataclasses import dataclass, field
from typing import Any, Optional

# Karelics messages
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
