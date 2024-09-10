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
import time
from abc import ABC, abstractmethod

# ROS
import rclpy
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.duration import Duration
from rclpy.node import Node

# Task Manager messages
from task_manager_msgs.action import Wait
from task_manager_msgs.srv import CancelTasks, StopTasks

# Task Manager
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import CancelTaskFailedError
from task_manager.task_specs import TaskServerType, TaskSpecs


class SystemTask(ABC):  # pylint: disable=too-few-public-methods
    """Abstract class for system tasks."""

    @staticmethod
    @abstractmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        """Returns TaskSpecs object that describes the task properties."""


class StopTasksService(SystemTask):
    """Implements Stop-command."""

    def __init__(self, node: Node, topic: str, active_tasks: ActiveTasks):
        self._node = node
        self._topic = topic
        self._active_tasks = active_tasks

        self._node.create_service(
            StopTasks, self._topic, self.service_cb, callback_group=MutuallyExclusiveCallbackGroup()
        )

    def service_cb(self, _request: StopTasks.Request, response: StopTasks.Response) -> StopTasks.Response:
        """Stops all the currently active tasks that have 'cancel_on_stop' field set to True."""
        try:
            self._active_tasks.cancel_tasks_on_stop()
            response.success = True
        except CancelTaskFailedError as e:
            self._node.get_logger().error(f"Failed to stop some tasks on STOP command: {e}")
            response.success = False

        return response

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        return TaskSpecs(
            task_name="system/stop",
            blocking=False,
            cancel_on_stop=False,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=StopTasks,
            task_server_type=TaskServerType.SERVICE,
            service_success_field="success",
        )


class CancelTasksService(SystemTask):
    """Cancel any task based on the task_id."""

    def __init__(self, node: Node, topic: str, active_tasks: ActiveTasks) -> None:
        self._node = node
        self._topic = topic
        self._active_tasks = active_tasks

        self._node.create_service(
            CancelTasks, self._topic, self.service_cb, callback_group=MutuallyExclusiveCallbackGroup()
        )

    def service_cb(self, request: CancelTasks.Request, response: CancelTasks.Response) -> CancelTasks.Response:
        """Cancels the currently active tasks by given task_id."""
        cancelled = []
        response.success = True
        for task_id in request.cancelled_tasks:
            try:
                self._active_tasks.cancel_task(task_id)
            except KeyError:
                self._node.get_logger().warning(
                    f"Tried to cancel a task with ID {task_id}, but the task is not active. "
                    f"Considering as a successful cancel."
                )
            except CancelTaskFailedError as e:
                self._node.get_logger().error(f"Failed to cancel task with ID {task_id}: {e}")
                response.success = False
                continue

            cancelled.append(task_id)

        response.successful_cancels = cancelled
        return response

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        return TaskSpecs(
            task_name="system/cancel_task",
            blocking=False,
            cancel_on_stop=False,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=CancelTasks,
            task_server_type=TaskServerType.SERVICE,
            service_success_field="success",
        )


class WaitTask(SystemTask):  # pylint: disable=too-few-public-methods
    """Wait for a specified amount of time or until a cancel is called."""

    def __init__(self, node: Node, topic: str):
        self._node = node
        self._topic = topic
        self._server = ActionServer(
            self._node,
            Wait,
            self._topic,
            self._execute_cb,
            cancel_callback=self._cancel_cb,
            callback_group=MutuallyExclusiveCallbackGroup(),
        )

    def _execute_cb(self, goal_handle: ServerGoalHandle) -> Wait.Result:
        """Callback for the Wait action server."""
        duration_in_seconds = goal_handle.request.duration
        loop_time = 0.1 if duration_in_seconds > 0.1 else duration_in_seconds
        start_time = self._node.get_clock().now()
        end_time = start_time + Duration(nanoseconds=int(duration_in_seconds * 1e9))
        feedback_frequency_ns = int(1.0 * 1e9)
        last_feedback_stamp = start_time

        while rclpy.ok() and self._node.get_clock().now() < end_time:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return Wait.Result()

            if not goal_handle.is_active:
                goal_handle.abort()
                return Wait.Result()

            # Publish feedback
            if (self._node.get_clock().now() - last_feedback_stamp).nanoseconds >= feedback_frequency_ns:
                last_feedback_stamp = self._node.get_clock().now()
                goal_handle.publish_feedback(
                    Wait.Feedback(remaining_time=(end_time - last_feedback_stamp).nanoseconds / 1e9)
                )

            time.sleep(loop_time)

        goal_handle.succeed()
        return Wait.Result()

    @staticmethod
    def _cancel_cb(_goal_handle: ServerGoalHandle) -> CancelResponse:
        return CancelResponse.ACCEPT

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        return TaskSpecs(
            task_name="system/wait",
            blocking=True,
            cancel_on_stop=True,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=Wait,
            task_server_type=TaskServerType.ACTION,
        )
