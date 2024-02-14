#  ------------------------------------------------------------------
#   Copyright (C) Karelics Oy - All Rights Reserved
#   Unauthorized copying of this file, via any medium is strictly
#   prohibited. All information contained herein is, and remains
#   the property of Karelics Oy.
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
