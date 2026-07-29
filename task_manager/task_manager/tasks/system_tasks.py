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
from typing import Optional

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
from task_manager.task_client import CancelTaskFailedError, PauseTaskFailedError, ResumeTaskFailedError, TaskClient
from task_manager.task_specs import TaskServerType, TaskSpecs
from task_manager.tasks.mission import Mission


def _mission_current_subtask_id(task_client: TaskClient, mission: Mission) -> Optional[str]:
    """If the given task_client is a Mission with a currently tracked subtask, returns that subtask's task_id."""
    if task_client.task_specs.task_name != "system/mission":
        return None
    goal_id = getattr(task_client, "goal_id", None)  # Mission is always action-backed, ActionTaskClient has this
    if goal_id is None:
        return None
    return mission.get_current_subtask_id(bytes(goal_id.uuid))


def _resolve_leaf_task_id(task_id: str, active_tasks: ActiveTasks, mission: Mission) -> str:
    """Follows a chain of Mission redirects down to the leaf task that should actually be paused/resumed.

    Missions can be nested (a mission's current subtask can itself be a mission), so this keeps resolving until
    it reaches a non-mission task, or a mission with no subtask tracked yet (e.g. pausing it right as it starts,
    before it has recorded its first subtask) - in which case the mission itself is returned as-is.

    :raises KeyError: if task_id (or one it resolves through) is not an active task.
    """
    seen = set()
    current = task_id
    while current not in seen:
        seen.add(current)
        subtask_id = _mission_current_subtask_id(active_tasks.get_task_client(current), mission)
        if subtask_id is None:
            return current
        current = subtask_id
    return current  # Cycle guard - shouldn't happen, but avoids ever looping forever on corrupt tracking data.


def _mirror_owning_mission_status(
    target_task_id: str, status: TaskStatus, active_tasks: ActiveTasks, mission: Mission
) -> None:
    """Reflects `status` onto every active Mission (at any nesting depth) whose currently tracked subtask chain resolves
    down to target_task_id.

    Lets pausing/resuming a subtask - whether directly by its own task_id, or indirectly through its owning
    Mission's task_id - keep the Mission's own displayed status in sync either way.
    """
    for candidate in active_tasks.get_active_tasks_by_name("system/mission"):
        if candidate.task_details.task_id == target_task_id:
            continue
        try:
            resolved = _resolve_leaf_task_id(candidate.task_details.task_id, active_tasks, mission)
        except KeyError:
            continue
        if resolved == target_task_id:
            candidate.task_details.status = status


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


class PauseTasksService(SystemTask):
    """Pause any task based on the task_id.

    Pausing a Mission by its own task_id is redirected to whichever subtask is currently running under it, and the
    Mission's own status is reflected as PAUSED too - but only if the subtask actually ended up PAUSED. A
    service-backed subtask that finishes on its own within its cancel_timeout grace period is left to complete
    normally, and the Mission simply continues to its next subtask.
    """

    def __init__(self, node: Node, topic: str, active_tasks: ActiveTasks, mission: Mission) -> None:
        self._node = node
        self._topic = topic
        self._active_tasks = active_tasks
        self._mission = mission

        self._node.create_service(
            PauseTasks, self._topic, self.service_cb, callback_group=MutuallyExclusiveCallbackGroup()
        )

    def service_cb(self, request: PauseTasks.Request, response: PauseTasks.Response) -> PauseTasks.Response:
        """Pauses the currently active tasks by given task_id."""
        paused = []
        response.success = True
        for task_id in request.paused_tasks:
            try:
                target_task_id = _resolve_leaf_task_id(task_id, self._active_tasks, self._mission)
                target_task_client = self._active_tasks.get_task_client(target_task_id)
                self._active_tasks.pause_task(target_task_id, publish=False)
            except KeyError:
                self._node.get_logger().error(f"Tried to pause a task with ID {task_id}, but the task is not active.")
                response.success = False
                continue
            except PauseTaskFailedError as e:
                self._node.get_logger().error(f"Failed to pause task with ID {task_id}: {e}")
                response.success = False
                continue

            # Only reflect PAUSED onto the owning Mission(s) if the target genuinely ended up paused - a
            # service-backed task that finished naturally within its cancel_timeout grace period ends up DONE
            # instead, and the Mission should just be left to continue to its next subtask.
            if target_task_client.task_details.status == TaskStatus.PAUSED:
                _mirror_owning_mission_status(target_task_id, TaskStatus.PAUSED, self._active_tasks, self._mission)
            self._active_tasks.publish_active_tasks()
            paused.append(task_id)

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

    Resuming a Mission by its own task_id is redirected to whichever subtask was previously paused under it, and the
    Mission's own status is reflected back to IN_PROGRESS too.
    """

    def __init__(self, node: Node, topic: str, active_tasks: ActiveTasks, mission: Mission) -> None:
        self._node = node
        self._topic = topic
        self._active_tasks = active_tasks
        self._mission = mission

        self._node.create_service(
            ResumeTasks, self._topic, self.service_cb, callback_group=MutuallyExclusiveCallbackGroup()
        )

    def service_cb(self, request: ResumeTasks.Request, response: ResumeTasks.Response) -> ResumeTasks.Response:
        """Resumes the currently paused tasks by given task_id."""
        resumed = []
        response.success = True
        for task_id in request.resumed_tasks:
            try:
                target_task_id = _resolve_leaf_task_id(task_id, self._active_tasks, self._mission)
                target_task_client = self._active_tasks.get_task_client(target_task_id)
                self._active_tasks.resume_task(target_task_id, publish=False)
            except KeyError:
                self._node.get_logger().error(f"Tried to resume a task with ID {task_id}, but the task is not active.")
                response.success = False
                continue
            except ResumeTaskFailedError as e:
                self._node.get_logger().error(f"Failed to resume task with ID {task_id}: {e}")
                response.success = False
                continue

            # Only reflect IN_PROGRESS onto the owning Mission(s) if the target genuinely resumed - resuming a
            # service-backed task (which can never really be PAUSED) is a no-op and leaves its status untouched.
            if target_task_client.task_details.status == TaskStatus.IN_PROGRESS:
                _mirror_owning_mission_status(target_task_id, TaskStatus.IN_PROGRESS, self._active_tasks, self._mission)
            self._active_tasks.publish_active_tasks()
            resumed.append(task_id)

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
