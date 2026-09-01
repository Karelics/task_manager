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
from typing import Dict, List

# ROS
import rclpy
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.clock import ClockType
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time

# Task Manager messages
from task_manager_msgs.action import Wait
from task_manager_msgs.msg import TaskStatus
from task_manager_msgs.srv import CancelTasks, PauseTasks, ResumeTasks, StopTasks

# Task Manager
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import CancelTaskFailedError, PauseTaskFailedError, ResumeTaskFailedError
from task_manager.task_specs import TaskServerType, TaskSpecs
from task_manager.tasks.active_children_tracker import ActiveChildrenTracker
from task_manager.tasks.composite_resolution import pause_or_resume_group, resolve_down


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
    """Cancel any task based on the task_id.

    Cancelling a Mission or a "perform in parallel" task by its own task_id still cancels that composite's own
    real goal directly (which is what correctly cascades down - e.g. a Mission's own goal being cancelled is what
    makes it notice and cancel its currently-running subtask). `successful_cancels` reports whichever leaf task(s)
    are actually running underneath the requested id, purely so the response reflects what really got stopped.
    """

    def __init__(
        self, node: Node, topic: str, active_tasks: ActiveTasks, composites: Dict[str, ActiveChildrenTracker]
    ) -> None:
        self._node = node
        self._topic = topic
        self._active_tasks = active_tasks
        self._composites = composites

        self._node.create_service(
            CancelTasks, self._topic, self.service_cb, callback_group=MutuallyExclusiveCallbackGroup()
        )

    def _resolve_reported_ids(self, task_id: str) -> List[str]:
        """Best-effort resolves task_id down to whichever leaf task(s) are actually running underneath it right now
        (e.g. a Mission's current subtask)."""
        try:
            return resolve_down(task_id, self._active_tasks, self._composites)
        except KeyError:
            return [task_id]

    def service_cb(self, request: CancelTasks.Request, response: CancelTasks.Response) -> CancelTasks.Response:
        """Cancels the currently active tasks by given task_id."""
        cancelled = []
        response.success = True
        for task_id in request.cancelled_tasks:
            reported_ids = self._resolve_reported_ids(task_id)
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

            cancelled.extend(reported_ids)

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


class PauseTasksService(SystemTask):
    """Pause any task based on the task_id.

    Pausing a Mission or a "perform in parallel" task by its own task_id is redirected to whichever of its
    children are currently active. Pausing one of those children directly - instead of the composite task -
    converges on the same outcome: the request is redirected up to the owning composite first, then back down to
    all of its currently active children. Either way, the composite's own displayed status (and transitively, any
    composite it's itself running under) is kept in sync with its children.

    Best-effort: a child that fails to pause (e.g. a service-backed one that doesn't finish within its
    cancel_timeout grace period) is reported as a failure, but siblings that did pause successfully are left
    paused rather than rolled back.
    """

    def __init__(
        self, node: Node, topic: str, active_tasks: ActiveTasks, composites: Dict[str, ActiveChildrenTracker]
    ) -> None:
        self._node = node
        self._topic = topic
        self._active_tasks = active_tasks
        self._composites = composites

        self._node.create_service(
            PauseTasks, self._topic, self.service_cb, callback_group=MutuallyExclusiveCallbackGroup()
        )

    def _try_pause(self, task_id: str) -> bool:
        try:
            self._active_tasks.pause_task(task_id, publish=False)
            return True
        except PauseTaskFailedError as e:
            self._node.get_logger().error(f"Failed to pause task with ID {task_id}: {e}")
            return False

    def service_cb(self, request: PauseTasks.Request, response: PauseTasks.Response) -> PauseTasks.Response:
        """Pauses the currently active tasks by given task_id."""
        paused = []
        response.success = True
        for task_id in request.paused_tasks:
            try:
                success = pause_or_resume_group(
                    task_id,
                    self._active_tasks,
                    self._composites,
                    (TaskStatus.RECEIVED, TaskStatus.IN_PROGRESS),
                    self._try_pause,
                    pause=True,
                )
            except KeyError:
                self._node.get_logger().error(f"Tried to pause a task with ID {task_id}, but the task is not active.")
                response.success = False
                continue

            if not success:
                response.success = False
                continue
            paused.append(task_id)

        # Publish once for the whole batch, so subscribers see one atomic final snapshot instead of N transient
        # intermediate ones.
        self._active_tasks.publish_active_tasks()
        response.successful_pauses = paused
        return response

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        return TaskSpecs(
            task_name="system/pause_task",
            blocking=False,
            cancel_on_stop=False,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=PauseTasks,
            task_server_type=TaskServerType.SERVICE,
            service_success_field="success",
        )


class ResumeTasksService(SystemTask):
    """Resume any paused task based on the task_id.

    Resuming a Mission or a "perform in parallel" task by its own task_id is redirected to whichever of its
    children are currently paused. Resuming one of those children directly converges on the same outcome, the
    same way pausing does - see `PauseTasksService`. Best-effort, same as pausing: a child that fails to resume is
    reported as a failure without affecting siblings that did resume.
    """

    def __init__(
        self, node: Node, topic: str, active_tasks: ActiveTasks, composites: Dict[str, ActiveChildrenTracker]
    ) -> None:
        self._node = node
        self._topic = topic
        self._active_tasks = active_tasks
        self._composites = composites

        self._node.create_service(
            ResumeTasks, self._topic, self.service_cb, callback_group=MutuallyExclusiveCallbackGroup()
        )

    def _try_resume(self, task_id: str) -> bool:
        try:
            self._active_tasks.resume_task(task_id, publish=False)
            return True
        except ResumeTaskFailedError as e:
            self._node.get_logger().error(f"Failed to resume task with ID {task_id}: {e}")
            return False

    def service_cb(self, request: ResumeTasks.Request, response: ResumeTasks.Response) -> ResumeTasks.Response:
        """Resumes the currently paused tasks by given task_id."""
        resumed = []
        response.success = True
        for task_id in request.resumed_tasks:
            try:
                success = pause_or_resume_group(
                    task_id,
                    self._active_tasks,
                    self._composites,
                    (TaskStatus.PAUSED,),
                    self._try_resume,
                    pause=False,
                )
            except KeyError:
                self._node.get_logger().error(f"Tried to resume a task with ID {task_id}, but the task is not active.")
                response.success = False
                continue

            if not success:
                response.success = False
                continue
            resumed.append(task_id)

        # Publish once for the whole batch, so subscribers see one atomic final snapshot instead of N transient
        # intermediate ones.
        self._active_tasks.publish_active_tasks()
        response.successful_resumes = resumed
        return response

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        return TaskSpecs(
            task_name="system/resume_task",
            blocking=False,
            cancel_on_stop=False,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=ResumeTasks,
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
        start_time = self._node.get_clock().now()

        # Wait indefinitely if duration is 0.0
        if duration_in_seconds <= 0.0:
            loop_time = 0.1
            end_time = Time(nanoseconds=2**63 - 1, clock_type=ClockType.ROS_TIME)
        else:
            loop_time = 0.1 if duration_in_seconds > 0.1 else duration_in_seconds
            end_time = start_time + Duration(nanoseconds=int(duration_in_seconds * 1e9))

        feedback_period_ns = int(1.0 * 1e9)
        last_feedback_stamp = start_time

        while rclpy.ok() and self._node.get_clock().now() < end_time:
            if goal_handle.is_cancel_requested:
                if duration_in_seconds <= 0.0:
                    goal_handle.succeed()
                else:
                    goal_handle.canceled()
                return Wait.Result()

            if not goal_handle.is_active:
                goal_handle.abort()
                return Wait.Result()

            # Publish feedback
            if (self._node.get_clock().now() - last_feedback_stamp).nanoseconds >= feedback_period_ns:
                last_feedback_stamp = self._node.get_clock().now()
                goal_handle.publish_feedback(
                    Wait.Feedback(remaining_time=float((end_time.nanoseconds - last_feedback_stamp.nanoseconds) / 1e9))
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
