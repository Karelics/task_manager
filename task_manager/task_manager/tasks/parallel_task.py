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

from typing import Optional

# ROS
import rclpy.logging
from rclpy.impl.rcutils_logger import RcutilsLogger
from rclpy.task import Future

# Task Manager messages
from task_manager_msgs.msg import SubtaskResult, TaskStatus

# Task Manager
from task_manager.task_client import TaskClient


class ParallelTask:
    """Encapsulates a single task that is executed as part of a set of tasks in ParallelTaskExecutor."""

    def __init__(
        self,
        task_client: TaskClient,
        timeout: float = 5.0,
        logger: RcutilsLogger = rclpy.logging.get_logger("ParallelTask"),
    ) -> None:
        """
        :param task_client: TaskClient responsible for executing the underlying task
        :param timeout: Timeout in seconds of waiting for server results
        :param logger: Logger for logging messages
        """
        self._task_client = task_client
        self._timeout = timeout
        self._logger = logger
        self._cancel_future: Optional[Future] = None

    @property
    def name(self) -> str:
        """
        :return: Name of the task
        """
        return self._task_client.task_specs.task_name

    @property
    def task_id(self) -> str:
        """
        :return: Id of the task
        """
        return self._task_client.task_details.task_id

    @property
    def active(self) -> bool:
        """
        :return: True if the underlying task is active, otherwise False
        """
        return self._task_client.task_details.status in [
            TaskStatus.RECEIVED,
            TaskStatus.IN_PROGRESS,
        ]

    def set_source(self, source: str) -> None:
        """Sets the source of the task.

        :param source: Source of the task
        """
        self._task_client.task_details.source = source

    def get_result(self) -> SubtaskResult:
        """
        :return: Result of the underlying task as SubtaskResult.
        """
        self._logger.info(f"Receiving '{self.name}' result")
        task_status = self._task_client.task_details.status

        return SubtaskResult(task_id=self.task_id, task_name=self.name, skipped=False, task_status=task_status)

    def cancel_async(self) -> None:
        """Triggers cancel for the task if it hasn't been cancelled or finished yet."""
        if not self.active:
            # Already canceled or finished, no need to cancel again
            return

        self._logger.info(f"Canceling '{self.name}'")
        self._cancel_future = self._task_client._request_canceling()

    def has_canceled(self) -> bool:
        """Checks if the task has been cancelled.

        For tasks with require_finish_on_parallel_cancel set to False, further processing is being
        done after canceling the task and they are not expected to finish within the cancelation timeouts.
        Thus returning True from this method. For example, insta video recording, will download the file
        from the camera and that will take some time.

        :return: True if the task has been cancelled successfully or require_finish_on_parallel_cancel is set to False,
          otherwise False
        """
        if not self._cancel_future:
            return False

        if not self.require_finish_on_parallel_cancel():
            return True

        return self._cancel_future.done()

    def require_finish_on_parallel_cancel(self) -> bool:
        """Checks if the task is expected to finish immediately after parallel cancel.

        :return: True if the task is expected to finish immediately after parallel cancel, otherwise False
        """
        return self._task_client.task_specs.require_finish_on_parallel_cancel
