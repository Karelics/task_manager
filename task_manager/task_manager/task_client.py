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

from abc import ABC, abstractmethod
from multiprocessing import Event
from typing import Any, Callable, Dict, List, Optional

# ROS
from rclpy import Future
from rclpy.action import ActionClient
from rclpy.action.client import ClientGoalHandle
from rclpy.client import Client
from rclpy.node import Node

# ROS messages
from action_msgs.msg import GoalInfo, GoalStatus

# Karelics messages
from task_manager_msgs.msg import TaskStatus

# Current package
from task_manager.task_details import TaskDetails
from task_manager.task_specs import TaskSpecs


class TaskClient(ABC):
    """Abstract Task Client that keeps track of a single task."""

    @property
    @abstractmethod
    def task_details(self) -> TaskDetails:
        """Information related to a single task execution."""

    @property
    @abstractmethod
    def task_specs(self) -> TaskSpecs:
        """General task-related information."""

    @abstractmethod
    def register_done_callback(self, callback: Callable[[TaskSpecs, TaskDetails], None]) -> None:
        """Registers callback which will be called when the task finishes."""

    @abstractmethod
    def start_task_async(self, goal_message: Any) -> None:
        """Start the task asynchronously."""

    @abstractmethod
    def cancel_task(self) -> None:
        """Cancel the task synchronously."""


class ActionTaskClient(TaskClient):
    """Task client that keeps track of a single Action task."""

    def __init__(
        self,
        node: Node,
        task_details: TaskDetails,
        task_specs: TaskSpecs,
        *,
        action_clients: Dict[str, ActionClient],
        cancel_task_timeout: Optional[float] = 5.0,
    ):
        """
        :param node: ROS node
        :param task_details: TaskDetails containing the public info about this client's task.
        :param task_specs: General info about the task
        :param action_clients: List of action clients that can be reused to call the task
        :param cancel_task_timeout: Timeout to wait for task to cancel
        """
        self._node = node
        self._task_details = task_details
        self._task_specs = task_specs
        self.cancel_task_timeout = cancel_task_timeout
        self.goal_done = Event()

        self._task_done_callbacks: List[Callable[[TaskSpecs, TaskDetails], None]] = []

        # Use an existing action client or create a new one if this is the first time this action is called
        if task_specs.task_name not in action_clients:
            action_clients[task_specs.task_name] = ActionClient(
                node, self.task_specs.msg_interface, self.task_specs.topic
            )

        self._client: ActionClient = action_clients[task_specs.task_name]

        self._goal_handle: Optional[ClientGoalHandle] = None
        self.server_wait_timeout = 10.0

    @property
    def task_details(self) -> TaskDetails:
        return self._task_details

    @property
    def task_specs(self) -> TaskSpecs:
        return self._task_specs

    def register_done_callback(self, callback: Callable[[TaskSpecs, TaskDetails], None]) -> None:
        if self.goal_done.is_set():
            callback(self.task_specs, self.task_details)
        self._task_done_callbacks.append(callback)

    def start_task_async(self, goal_message: Any) -> None:
        """Calls an action server to start the task.

        :param goal_message: ROS action goal message
        :raises TaskStartError: If task cannot be started
        """
        if not self._client.wait_for_server(timeout_sec=self.server_wait_timeout):
            self.task_details.status = TaskStatus.ERROR
            raise TaskStartError(f"Action server {self.task_specs.topic} not available.")

        try:
            send_goal_future = self._client.send_goal_async(goal=goal_message)
        except TimeoutError:
            self.task_details.status = TaskStatus.ERROR
            raise TaskStartError(
                f"Action server {self.task_specs.topic} not available, unable to start the task."
            ) from None

        try:
            self._wait_for_future_to_complete(send_goal_future, timeout=self.server_wait_timeout)
        except TimeoutError:
            self.task_details.status = TaskStatus.ERROR
            self._node.get_logger().error(
                f"Timed out while waiting for response from the action server {self.task_specs.topic}, "
                f"took longer than {self.server_wait_timeout} seconds"
            )
            raise TaskStartError(
                f"Task start timed out while trying to get response from action server: {self.task_specs.topic}"
            ) from None

        self._goal_handle = send_goal_future.result()

        if not self._goal_handle.accepted:
            self.task_details.status = TaskStatus.ERROR
            raise TaskStartError(f"Goal was not accepted by the action server for the task {self.task_specs.task_name}")

        self.task_details.status = TaskStatus.IN_PROGRESS
        future: Future = self._goal_handle.get_result_async()
        future.add_done_callback(self._goal_done_cb)

    def cancel_task(self) -> None:
        """
        :raises CancelTaskFailedError: If cancel request fails, due to timeout or other
        """
        # In some rare cases the goal might already be done at this point. If not, cancel it.
        done_states = [GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_ABORTED, GoalStatus.STATUS_CANCELED]
        if self._goal_handle.status not in done_states:
            # There seems to be a bug in rclpy, making the return code to be 0 (ERROR_NONE),
            # no matter if the cancel was rejected or accepted. So checking instead if the
            # goal is within the cancelling goals.
            goals_canceling = self._request_canceling(self.cancel_task_timeout)
            goal_ids_cancelling = [goal_info.goal_id for goal_info in goals_canceling]
            if self._goal_handle.goal_id not in goal_ids_cancelling:
                self._node.get_logger().error(
                    f"Couldn't cancel the task. Action server {self.task_specs.topic} did not "
                    f"accept to cancel the goal."
                )
                raise CancelTaskFailedError("Couldn't cancel the task!")

        # Wait until _goal_done_cb is called and callbacks have been notified
        if not self.goal_done.wait(timeout=self.cancel_task_timeout):
            raise CancelTaskFailedError(
                f"Task didn't finish within {self.cancel_task_timeout} second timeout after it was cancelled. "
                f"Is the task cancel implemented correctly?"
            )

    def _request_canceling(self, timeout: float) -> List[GoalInfo]:
        future = self._goal_handle.cancel_goal_async()
        try:
            self._wait_for_future_to_complete(future, timeout=timeout)
        except TimeoutError as e:
            self._node.get_logger().error(
                f"Timeout while waiting response to cancel request from server {self.task_specs.task_name}: {str(e)}."
            )
            raise CancelTaskFailedError("Cancel request timed out.") from e
        return future.result().goals_canceling

    def _goal_done_cb(self, future: Future) -> None:
        """Called when the Action Client's goal finishes. Updates the task status and notifies callbacks further the
        other callbacks.

        :param future: Future object giving the result of the action call.
        """
        result = future.result()
        goal_status = result.status

        try:
            end_goal_status = ros_goal_status_to_task_status(goal_status)
        except RuntimeError as e:
            self._node.get_logger().error(
                f"Unable to determine final status of task {self.task_specs.task_name}: {repr(e)}"
            )
            self.task_details.status = TaskStatus.ERROR
        else:
            if self.task_specs.cancel_reported_as_success and end_goal_status == TaskStatus.CANCELED:
                self.task_details.status = TaskStatus.DONE
            else:
                self.task_details.status = end_goal_status

        self.task_details.result = result.result

        for callback in self._task_done_callbacks:
            callback(self.task_specs, self.task_details)
        self.goal_done.set()

    @staticmethod
    def _wait_for_future_to_complete(future: Future, timeout: Optional[float]) -> None:
        event = Event()

        def unblock(_):
            nonlocal event
            event.set()

        future.add_done_callback(unblock)
        event.wait(timeout=timeout)

        if not event.is_set():
            raise TimeoutError()

        if future.exception() is not None:
            raise future.exception()


class ServiceTaskClient(TaskClient):
    """Keeps track of a single task status.

    Provides the functionality to make a service call and set the status and result based on it.
    """

    def __init__(
        self,
        node: Node,
        task_details: TaskDetails,
        task_specs: TaskSpecs,
        *,
        service_clients: Dict[str, Client],
        cancel_task_timeout: Optional[float] = 5.0,
    ):
        """
        :param node: ROS node
        :param task_details: TaskDetails containing the public info about this client's task.
        :param task_specs: General info about the task
        :param service_clients: List of service clients that can be reused to call the task
        :param cancel_task_timeout: Timeout to wait for task to cancel
        """
        self._node = node
        self._task_details = task_details
        self._task_specs = task_specs
        self._service_clients = service_clients
        self._cancel_task_timeout = cancel_task_timeout
        self.goal_done = Event()

        self._task_done_callbacks: List[Callable[[TaskSpecs, TaskDetails], None]] = []

        # # Use an existing service client or create a new one if this is the first time this service is called
        if task_specs.task_name not in self._service_clients:
            self._service_clients[task_specs.task_name] = self._node.create_client(
                self._task_specs.msg_interface, self._task_specs.topic
            )

        self._client = self._service_clients[task_specs.task_name]

    @property
    def task_details(self) -> TaskDetails:
        return self._task_details

    @property
    def task_specs(self) -> TaskSpecs:
        return self._task_specs

    def register_done_callback(self, callback: Callable[[TaskSpecs, TaskDetails], None]) -> None:
        """Registers callback which will be called when the task finishes."""
        if self.goal_done.is_set():
            callback(self.task_specs, self.task_details)
        self._task_done_callbacks.append(callback)

    def start_task_async(self, goal_message: Any) -> None:
        """Calls the service asynchronously.

        :raises TaskStartError: If the service call fails.
        """
        if not self._client.wait_for_service(timeout_sec=1):
            self.task_details.status = TaskStatus.ERROR
            raise TaskStartError(f"Service {self._task_specs.topic} not available")

        try:
            future = self._client.call_async(goal_message)
        except RuntimeError as e:
            self.task_details.status = TaskStatus.ERROR
            raise TaskStartError("Failed to start the task.") from e

        self.task_details.status = TaskStatus.IN_PROGRESS
        future.add_done_callback(self._done_callback)

    def cancel_task(self) -> None:
        """Since services by their nature do not support cancelling, waits for the service to finish if it hasn't
        already.

        :raises CancelTaskFailedError: If the service doesn't finish in a given timeout.
        """
        if self.goal_done.is_set():
            return
        self._node.get_logger().warn(
            f"Currently ongoing service call to {self._task_specs.topic} cannot be cancelled. "
            f"Waiting for {self._cancel_task_timeout} seconds for the task to finish."
        )
        if not self.goal_done.wait(self._cancel_task_timeout):
            raise CancelTaskFailedError(f"Service call to {self._task_specs.topic} cannot be cancelled.")

    def _done_callback(self, future):
        self.task_details.result = future.result()
        self.task_details.status = TaskStatus.DONE

        # If the service response has a "success" -field, use that to determine the final task status
        if self._task_specs.service_success_field != "":
            task_success = getattr(self.task_details.result, self._task_specs.service_success_field)
            if not task_success:
                self.task_details.status = TaskStatus.ERROR

        for callback in self._task_done_callbacks:
            callback(self.task_specs, self.task_details)

        self.goal_done.set()


def ros_goal_status_to_task_status(ros_goal_status: GoalStatus) -> TaskStatus:
    """Transforms ROS goal status to task status.

    :param ros_goal_status: Status as GoalStatus message.
    :return: TaskStatus
    :raise RuntimeError: Status conversion fails.
    """
    if ros_goal_status in [GoalStatus.STATUS_UNKNOWN, GoalStatus.STATUS_ACCEPTED]:
        return TaskStatus.RECEIVED
    if ros_goal_status in [GoalStatus.STATUS_EXECUTING, GoalStatus.STATUS_CANCELING]:
        return TaskStatus.IN_PROGRESS
    if ros_goal_status == GoalStatus.STATUS_SUCCEEDED:
        return TaskStatus.DONE
    if ros_goal_status == GoalStatus.STATUS_ABORTED:
        return TaskStatus.ERROR
    if ros_goal_status == GoalStatus.STATUS_CANCELED:
        return TaskStatus.CANCELED
    raise RuntimeError("Unknown goal state")


class TaskStartError(Exception):
    """Raised whenever the task start fails."""


class CancelTaskFailedError(Exception):
    """Raised when canceling of the task fails, whether due to timeout or other reason."""
