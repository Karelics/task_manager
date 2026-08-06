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
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
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

    @property
    @abstractmethod
    def goal_done(self) -> bool:
        """Returns True if the task has finished, False otherwise."""

    @property
    @abstractmethod
    def goal_id(self) -> Optional[Any]:
        """ROS action goal_id (unique_identifier_msgs/UUID) of the currently active goal, shared by the client and
        server side of the same action call.

        None for task types that aren't backed by a ROS action (e.g. ServiceTaskClient), or if the task hasn't started
        yet.
        """

    @abstractmethod
    def register_done_callback(self, callback: Callable[[TaskSpecs, TaskDetails], None]) -> None:
        """Registers callback which will be called when the task finishes."""

    @abstractmethod
    def start_task_async(self, goal_message: Any) -> None:
        """Start the task asynchronously."""

    @abstractmethod
    def cancel_task(self) -> None:
        """Cancel the task synchronously."""

    @abstractmethod
    def request_canceling(self) -> None:
        """Request canceling the task asynchronously."""

    @abstractmethod
    def pause_task(self) -> None:
        """Pause the task synchronously."""

    @abstractmethod
    def resume_task(self) -> None:
        """Resume a previously paused task synchronously."""


class ActionTaskClient(TaskClient):
    """Task client that keeps track of a single Action task."""

    DONE_STATES = [GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_ABORTED, GoalStatus.STATUS_CANCELED]

    def __init__(
        self,
        node: Node,
        task_details: TaskDetails,
        task_specs: TaskSpecs,
        action_clients: Dict[str, ActionClient],
    ):
        """
        :param node: ROS node
        :param task_details: TaskDetails containing the public info about this client's task.
        :param task_specs: General info about the task
        :param action_clients: List of action clients that can be reused to call the task
        """
        self._node = node
        self._task_details = task_details
        self._task_specs = task_specs
        self._goal_done = Event()
        self._pausing = False
        self._paused = False
        self._pause_done = Event()
        self._last_goal_message: Optional[Any] = None
        # Result of each paused segment (the Result the server returned when its goal was cancelled by
        # pause_task()), in chronological order. Merged into the final result via
        # task_specs.result_concat_fields once the task truly finishes.
        self._paused_results: List[Any] = []

        self._task_done_callbacks: List[Callable[[TaskSpecs, TaskDetails], None]] = []

        # Use an existing action client or create a new one if this is the first time this action is called
        if task_specs.task_name not in action_clients:
            action_clients[task_specs.task_name] = ActionClient(
                node, self.task_specs.msg_interface, self.task_specs.topic
            )

        self._client: ActionClient = action_clients[task_specs.task_name]

        self._goal_handle: Optional[ClientGoalHandle] = None
        self._result_future: Optional[Future] = None
        self.server_wait_timeout = 10.0

    @property
    def task_details(self) -> TaskDetails:
        return self._task_details

    @property
    def task_specs(self) -> TaskSpecs:
        return self._task_specs

    @property
    def goal_done(self) -> bool:
        return self._goal_done.is_set()

    @property
    def goal_id(self) -> Optional[Any]:
        """ROS action goal_id (unique_identifier_msgs/UUID) of the currently active goal, shared by the client and
        server side of the same action call.

        None if the task hasn't started yet.
        """
        return self._goal_handle.goal_id if self._goal_handle else None

    def register_done_callback(self, callback: Callable[[TaskSpecs, TaskDetails], None]) -> None:
        if self._goal_done.is_set():
            callback(self.task_specs, self.task_details)
        self._task_done_callbacks.append(callback)

    def start_task_async(self, goal_message: Any) -> None:
        """Calls an action server to start the task.

        :param goal_message: ROS action goal message
        :raises TaskStartError: If task cannot be started
        """
        self._last_goal_message = goal_message

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
        self._result_future = self._goal_handle.get_result_async()
        self._result_future.add_done_callback(self._goal_done_cb)

    def cancel_task(self) -> None:
        """Cancel the task.

        This function waits till the task to finish or until the cancel timeout is reached.

        :raises CancelTaskFailedError: If task does not finish (cancel) within the timeout or
            a bad cancel response code is received.
        """
        if self._finish_if_paused():
            return

        if not self._goal_handle:
            raise CancelTaskFailedError("Couldn't cancel the task, goal handle does not exist!")

        # In some rare cases the goal might already be done at this point. If not, cancel it.
        if self._goal_handle.status not in self.DONE_STATES:
            self.request_canceling()
            self._node.get_logger().info(f"Cancelling task of a type '{self.task_specs.task_name}'.")
        else:
            self._node.get_logger().info(
                f"Tried to cancel task of a type '{self.task_specs.task_name}' which is already in a done state. "
                f"Waiting for goal callbacks to finish."
            )

        # Wait until _goal_done_cb is called and callbacks have been notified
        if not self._goal_done.wait(timeout=self._task_specs.cancel_timeout):
            raise CancelTaskFailedError(
                f"Task didn't finish within {self._task_specs.cancel_timeout} second timeout after it was cancelled. "
                f"Is the task cancel implemented correctly?"
            )

    def request_canceling(self) -> None:
        """Requests canceling of the goal and handles the response from the cancel service call.

        If the task is currently PAUSED, its goal was already cancelled once when it got paused - this just
        finishes it locally as CANCELED instead of issuing a new (bogus) cancel request against a dead goal.

        :raises CancelTaskFailedError: If the cancel request fails due to timeout or bad cancel response code.
        """
        if self._finish_if_paused():
            return

        response = self._request_canceling(timeout=self._task_specs.cancel_timeout)
        self._handle_cancel_response(response)

    def _finish_if_paused(self) -> bool:
        """If this client's own goal was actually paused (via a successful `pause_task()` call on this same instance),
        finishes it as CANCELED and returns True.

        Deliberately checks the internal `_paused` flag rather than `task_details.status` - a composite task's
        (Mission/ParallelTaskExecutor) status can be forced to PAUSED purely for display purposes by
        system_tasks.py's `_sync_composite_statuses()`, without this client's own goal ever actually being
        cancelled. Trusting the public status there would wrongly finish a composite whose real underlying goal
        is still running.

        When `_paused` is genuinely True, the goal was already cancelled once when it got paused, and that
        cancel's done-callback was suppressed by pause_task() at the time (see `_goal_done_cb`) - so nothing will
        ever fire it on its own; this is the only place that ever will. Returns False (no-op) otherwise.
        """
        if not self._paused:
            return False
        self._paused = False
        self.task_details.status = TaskStatus.CANCELED
        self.task_details.result = self._merged_result()
        self._notify_done_callbacks()
        return True

    def _request_canceling(self, timeout: float) -> CancelGoal.Response:
        """Requests canceling for the goal and returns the cancel response.

        :param timeout: Time the function waits for the cancel response from the action server.
        :return: Response of the cancel service call.
        :raises CancelTaskFailedError: If the cancel request fails due to timeout or the goal handle does not exist.
        """
        if not self._goal_handle:
            raise CancelTaskFailedError("Couldn't cancel the task, goal handle does not exist!")

        future = self._goal_handle.cancel_goal_async()

        try:
            self._wait_for_future_to_complete(future, timeout=timeout)
        except TimeoutError as e:
            self._node.get_logger().error(
                f"Timeout while waiting response to cancel request from server {self.task_specs.task_name}: {repr(e)}."
            )
            raise CancelTaskFailedError("Cancel request timed out.") from e
        return future.result()

    def _handle_cancel_response(self, response: CancelGoal.Response) -> None:
        """Handles the response from the cancel request.

        :raises CancelTaskFailedError: If the cancel request fails
        """
        # There seems to be a bug in rclpy, making the return code to be 0 (ERROR_NONE),
        # no matter if the cancel was rejected or accepted. So checking instead if the
        # goal is within the cancelling goals.
        if response.return_code == CancelGoal.Response.ERROR_UNKNOWN_GOAL_ID:
            self._node.get_logger().info(
                f"Action server {self.task_specs.topic} did not recognize the goal id. "
                f"Maybe server has restarted during the task execution and the goal no longer exists. "
                f"Considering the task canceled."
            )
            if not self._result_future:
                raise CancelTaskFailedError("Couldn't cancel the task result future since it does not exist!")
            self._result_future.cancel()

        elif response.return_code == CancelGoal.Response.ERROR_GOAL_TERMINATED:
            self._node.get_logger().info(
                f"Action server {self.task_specs.topic} did not accept to cancel the goal. "
                f"Goal seems to have already finished. Considering the task canceled."
            )
            if not self._result_future:
                raise CancelTaskFailedError("Couldn't cancel the task result future since it does not exist!")
            self._result_future.cancel()

        else:
            if not self._goal_handle:
                raise CancelTaskFailedError("Couldn't cancel the task, goal handle does not exist!")
            goal_ids_cancelling = [goal_info.goal_id for goal_info in response.goals_canceling]
            if self._goal_handle.goal_id not in goal_ids_cancelling:
                self._node.get_logger().error(
                    f"Couldn't cancel the task. Action server {self.task_specs.topic} did not "
                    f"accept to cancel the goal."
                )
                raise CancelTaskFailedError("Couldn't cancel the task!")

    def pause_task(self) -> None:
        """Pauses the task by cancelling the underlying action goal, keeping the original goal message stored so that
        resume_task() can restart it later.

        The task's entry stays in ActiveTasks the whole time; no "task done" callbacks are fired. The partial
        result the server returns when the goal is cancelled is kept, and any fields listed in
        `task_specs.result_concat_fields` are concatenated into the task's final result once it truly finishes.

        :raises PauseTaskFailedError: If the task is already paused/finished, or the cancel fails/times out.
        """
        if self._paused:
            raise PauseTaskFailedError("Task is already paused.")
        if self.goal_done:
            raise PauseTaskFailedError("Cannot pause a task that has already finished.")
        if not self._goal_handle:
            raise PauseTaskFailedError("Couldn't pause the task, goal handle does not exist!")

        self._pausing = True
        self._pause_done = Event()
        try:
            self.request_canceling()
        except CancelTaskFailedError as e:
            self._pausing = False
            raise PauseTaskFailedError(f"Failed to pause the task: {e}") from e

        # Wait for _goal_done_cb to set the pause_done event, which indicates that the cancel has been done.
        if not self._pause_done.wait(timeout=self._task_specs.cancel_timeout):
            self._pausing = False
            if self.goal_done:
                # The goal finished on its own (succeeded/aborted) before our cancel could take effect -
                # _goal_done_cb() has already fired the real done-callbacks with the real result. Nothing to undo,
                # but this pause attempt itself did not succeed.
                raise PauseTaskFailedError("Task finished on its own before it could be paused.")
            raise PauseTaskFailedError(
                f"Task didn't pause within {self._task_specs.cancel_timeout} second timeout after it was cancelled."
            )

        self._pausing = False
        self._paused = True
        self.task_details.status = TaskStatus.PAUSED

    def resume_task(self) -> None:
        """Resumes a paused task by re-sending the original goal message as a brand-new action goal.

        No-op if the task is not currently paused.

        :raises ResumeTaskFailedError: If (re)starting the goal fails.
        """
        if not self._paused:
            return

        self._paused = False
        self._goal_handle = None
        self._result_future = None
        try:
            self.start_task_async(self._last_goal_message)
        except TaskStartError as e:
            # start_task_async() already set status to ERROR, but goal_done is only ever set via the done-callback
            # chain, which nothing else will trigger for a start failure - without this, the task would stay
            # wedged in ActiveTasks forever (never finished, never cancellable, since there's no live goal_handle).
            self.task_details.result = self._merged_result()
            self._notify_done_callbacks()
            raise ResumeTaskFailedError(f"Failed to resume the task: {e}") from e

    def _goal_done_cb(self, future: Future) -> None:
        """Called when the Action Client's goal finishes. Updates the task status and invokes task done callbacks.

        :param future: Future object giving the result of the action call.
        """
        if self._pausing and self._canceled_result(future):
            # This is the cancel triggered from pause_task(). The task is not actually
            # finished, so we skip the normal "task done" side effects entirely. The partial result the
            # server attached to the cancelled goal is kept, to be concatenated into the final result
            # (see _merged_result).
            if not future.cancelled():
                self._paused_results.append(future.result().result)
            self._pause_done.set()
            return

        # Reached even while _pausing is True if the goal genuinely finished (succeeded/aborted) on its own,
        # racing ahead of our cancel request - that real result must not be silently discarded.
        if future.cancelled():
            self.task_details.status = TaskStatus.CANCELED
            self.task_details.result = self.task_specs.msg_interface.Result()
        else:
            self._fill_in_task_details(future)

        self._notify_done_callbacks()

    @staticmethod
    def _canceled_result(future: Future) -> bool:
        """Whether a goal completion represents a genuine CANCELED outcome.

        Either the Python future itself was cancelled, or the server reported the goal as cancelled.
        """
        if future.cancelled():
            return True
        return future.result().status == GoalStatus.STATUS_CANCELED

    def _notify_done_callbacks(self) -> None:
        """Invokes all registered task-done callbacks and marks the goal as done."""
        for callback in self._task_done_callbacks:
            callback(self.task_specs, self.task_details)
        self._goal_done.set()

    def _fill_in_task_details(self, future: Future) -> None:
        """Fills in the task details based on the future result."""
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
        self.task_details.result = self._merged_result(result.result)

    def _merged_result(self, final_result: Optional[Any] = None) -> Any:
        """Merges the results collected from paused segments (see `pause_task`) into the task's final result.

        Each field listed in `task_specs.result_concat_fields` is concatenated across all segments in
        chronological order; every other field keeps final_result's value as-is. With final_result=None (the task
        never produced a final segment result, e.g. it was cancelled or failed to restart while paused), the last
        paused segment's result serves as the base instead - or an empty Result if there are no segments at all.

        Best-effort: a listed field that doesn't exist or isn't concatenatable is logged and skipped, never fatal.
        """
        partials = self._paused_results
        if final_result is None:
            if not partials:
                return self.task_specs.msg_interface.Result()
            final_result, partials = partials[-1], partials[:-1]
        if not partials:
            return final_result

        for field_name in self.task_specs.result_concat_fields:
            try:
                values = [getattr(partial, field_name) for partial in partials] + [getattr(final_result, field_name)]
                if isinstance(values[0], str):
                    setattr(final_result, field_name, "".join(values))
                else:
                    setattr(final_result, field_name, [item for value in values for item in value])
            except (AttributeError, TypeError, AssertionError) as e:
                self._node.get_logger().error(
                    f"Failed to concatenate result field '{field_name}' of task "
                    f"'{self.task_specs.task_name}' across its paused segments: {repr(e)}. "
                    f"Keeping only the final segment's value for this field."
                )
        return final_result

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
        service_clients: Dict[str, Client],
    ):
        """
        :param node: ROS node
        :param task_details: TaskDetails containing the public info about this client's task.
        :param task_specs: General info about the task
        :param service_clients: List of service clients that can be reused to call the task
        """
        self._node = node
        self._task_details = task_details
        self._task_specs = task_specs
        self._service_clients = service_clients
        self._goal_done = Event()

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

    @property
    def goal_done(self) -> bool:
        return self._goal_done.is_set()

    @property
    def goal_id(self) -> Optional[Any]:
        """Services aren't backed by a ROS action goal."""
        return None

    def register_done_callback(self, callback: Callable[[TaskSpecs, TaskDetails], None]) -> None:
        """Registers callback which will be called when the task finishes."""
        if self._goal_done.is_set():
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
        if self._goal_done.is_set():
            return
        self._node.get_logger().warn(
            f"Currently ongoing service call to {self._task_specs.topic} cannot be cancelled. "
            f"Waiting for {self._task_specs.cancel_timeout} seconds for the task to finish."
        )
        if not self._goal_done.wait(self._task_specs.cancel_timeout):
            raise CancelTaskFailedError(f"Service call to {self._task_specs.topic} cannot be cancelled.")

    def request_canceling(self) -> None:
        """Canceling of a service is not a supported feature in ROS 2.

        This function simply returns straight away.
        """
        self._node.get_logger().debug(
            f"Cancel call to service {self._task_specs.topic} is not supported. Ignoring the cancel request."
        )

    def pause_task(self) -> None:
        """Services cannot be cancelled mid-flight, so instead of failing immediately, this waits out cancel_timeout for
        the ongoing call to finish naturally.

        If it finishes within that grace period, the pause is considered successful even though the task ended up
        DONE rather than PAUSED - a Mission blocked on this subtask can then simply continue on to the next step,
        the same as if no pause had been requested. Only a call that outlives the grace period is a real failure.

        :raises PauseTaskFailedError: If the task has already finished, or the service call is still running after
            cancel_timeout.
        """
        if self._goal_done.is_set():
            raise PauseTaskFailedError("Cannot pause a task that has already finished.")

        self._node.get_logger().warn(
            f"Currently ongoing service call to {self._task_specs.topic} cannot be paused. "
            f"Waiting for {self._task_specs.cancel_timeout} seconds for the task to finish on its own."
        )
        if not self._goal_done.wait(self._task_specs.cancel_timeout):
            raise PauseTaskFailedError(f"Service call to {self._task_specs.topic} cannot be paused.")

    def resume_task(self) -> None:
        """No-op, since a service-backed task can never be in a PAUSED state."""
        return

    def _done_callback(self, future):
        self.task_details.result = future.result()
        self.task_details.status = TaskStatus.DONE

        # If the service response has a "success" field, use that to determine the final task status
        if self._task_specs.service_success_field != "":
            task_success = getattr(self.task_details.result, self._task_specs.service_success_field)
            if not task_success:
                self.task_details.status = TaskStatus.ERROR

        for callback in self._task_done_callbacks:
            callback(self.task_specs, self.task_details)

        self._goal_done.set()


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


class PauseTaskFailedError(Exception):
    """Raised when pausing of the task fails, whether due to timeout, the task already being finished/paused, or the
    task type not supporting pausing."""


class ResumeTaskFailedError(Exception):
    """Raised when resuming of the task fails, e.g. restarting the underlying goal fails."""
