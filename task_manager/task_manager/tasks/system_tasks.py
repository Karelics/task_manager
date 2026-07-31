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
from typing import Callable, Dict, List, Optional, Protocol, Tuple

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


class ActiveChildrenTracker(Protocol):  # pylint: disable=too-few-public-methods
    """Structural interface for composite tasks (Mission, ParallelTaskExecutor, ...) whose pause/resume must redirect to
    whichever of their own children are currently active.

    Duck-typed on purpose (no shared base class) so this module never needs to import Mission's or
    ParallelTaskExecutor's concrete classes - the latter would create an import cycle, since it itself imports
    SystemTask from this module.
    """

    def get_active_children(self, goal_id: bytes) -> List[str]:
        """Returns the task_ids of this composite invocation's currently active (not yet finished) children."""


def _composite_active_children(
    task_client: TaskClient, composites: Dict[str, "ActiveChildrenTracker"]
) -> Optional[List[str]]:
    """If task_client is one of the registered composite task types, returns its currently active children's task_ids
    (possibly empty).

    None if it isn't a registered composite at all.
    """
    tracker = composites.get(task_client.task_specs.task_name)
    if tracker is None:
        return None
    goal_id = task_client.goal_id  # Composites are always action-backed, so this is never None once started
    if goal_id is None:
        return []
    return tracker.get_active_children(bytes(goal_id.uuid))


def _resolve_down(task_id: str, active_tasks: ActiveTasks, composites: Dict[str, "ActiveChildrenTracker"]) -> List[str]:
    """Expands task_id down to the leaf task(s) that should actually be paused/resumed.

    A plain leaf resolves to itself. A composite (Mission, ParallelTaskExecutor, ...) resolves to its currently
    active children, expanded recursively - so composites nested inside each other (e.g. a Mission subtask that
    is itself a parallel task) are handled uniformly, regardless of depth or mix of composite types. A composite
    with no active children yet (e.g. paused right as it starts, before it has recorded any) resolves to itself.

    :raises KeyError: if task_id (or one it resolves through) is not an active task.
    """
    children = _composite_active_children(active_tasks.get_task_client(task_id), composites)
    if not children:
        return [task_id]
    return [leaf for child in children for leaf in _resolve_down(child, active_tasks, composites)]


def _find_enclosing_composite(
    task_id: str, active_tasks: ActiveTasks, composites: Dict[str, "ActiveChildrenTracker"]
) -> Optional[str]:
    """If task_id is currently a tracked active child of some other active composite task, returns that.

    composite's own task_id - one hop up. None if task_id isn't a tracked child of anything right now.

    This is what makes pausing/resuming one member of a group and pausing/resuming the group itself converge on
    the same outcome: the request gets redirected to the parent first, then `_resolve_down` re-expands it back to
    every one of its currently active children.
    """
    for task_name in composites:
        for candidate in active_tasks.get_active_tasks_by_name(task_name):
            if task_id in (_composite_active_children(candidate, composites) or []):
                return candidate.task_details.task_id
    return None


def _resolve_target_task_ids(
    task_id: str, active_tasks: ActiveTasks, composites: Dict[str, "ActiveChildrenTracker"]
) -> List[str]:
    """Resolves a task_id given in a pause/resume request to the full set of leaf task_ids that must actually be
    paused/resumed together.

    :raises KeyError: if task_id (or one it resolves through) is not an active task.
    """
    enclosing = _find_enclosing_composite(task_id, active_tasks, composites)
    return _resolve_down(enclosing if enclosing is not None else task_id, active_tasks, composites)


def _sync_composite_statuses(active_tasks: ActiveTasks, composites: Dict[str, "ActiveChildrenTracker"]) -> None:
    """Re-derives every active composite task's own displayed status from its currently active children's statuses, at
    any nesting depth: PAUSED once all of them are PAUSED, IN_PROGRESS as soon as none of them are.

    Composites have no "real" paused state of their own to toggle - their own action call is just a Python loop
    watching their children (cancelling/resuming it directly wouldn't make sense) - so their displayed status is
    always just a reflection of what their children are doing. Runs repeated passes (bounded by the number of
    composites) so a multi-level nesting chain settles regardless of scan order.
    """
    all_composites = [client for task_name in composites for client in active_tasks.get_active_tasks_by_name(task_name)]
    for _ in range(len(all_composites) + 1):
        changed = False
        for client in all_composites:
            children = _composite_active_children(client, composites)
            if not children:
                continue
            child_statuses = {active_tasks.get_task_client(child).task_details.status for child in children}
            if child_statuses == {TaskStatus.PAUSED} and client.task_details.status != TaskStatus.PAUSED:
                client.task_details.status = TaskStatus.PAUSED
                changed = True
            elif TaskStatus.PAUSED not in child_statuses and client.task_details.status == TaskStatus.PAUSED:
                client.task_details.status = TaskStatus.IN_PROGRESS
                changed = True
        if not changed:
            break


def _pause_or_resume_group(
    task_id: str,
    active_tasks: ActiveTasks,
    composites: Dict[str, "ActiveChildrenTracker"],
    from_statuses: Tuple[TaskStatus, ...],
    func_to_call: Callable[[str], bool],
) -> bool:
    """Resolves task_id (Mission/parallel-aware) to its full set of target leaves, and applies try_member to.

    every one of them currently in one of from_statuses - best-effort: a member that fails doesn't stop the
    others from being attempted, and members that already succeeded are never rolled back. Re-syncs every
    composite's own status from its children afterward.

    :param task_id: the task_id given in the pause/resume request.
    :param active_tasks: the ActiveTasks instance to operate on.
    :param composites: the dict of composite task names to their ActiveChildrenTracker instances.
    :param from_statuses: the set of statuses that a member must be in to be attempted for transition.
        Members already in the target state are skipped, and members that have finished on their own are
        also skipped (not a failure).
    :param func_to_call: the function to call for each member that is in from_statuses. Should return True
        if the transition succeeded, False if it failed.
    :raises KeyError: if task_id is not an active task.
    :return: True if any member failed to transition.
    """
    target_ids = _resolve_target_task_ids(task_id, active_tasks, composites)

    any_failure = False
    for member_id in target_ids:
        if active_tasks.get_task_client(member_id).task_details.status not in from_statuses:
            continue  # Already in the target state, or finished on its own - nothing to do, not a failure.
        if not func_to_call(member_id):
            any_failure = True

    _sync_composite_statuses(active_tasks, composites)
    return any_failure


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
        """Best-effort resolves task_id down to whichever leaf task(s) are actually running underneath it right.

        now (e.g. a Mission's current subtask) - purely for reporting. Must be called before actually cancelling,
        since a cancelled composite is removed from ActiveTasks once done and couldn't be resolved afterward.
        """
        try:
            return _resolve_down(task_id, self._active_tasks, self._composites)
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
                any_failure = _pause_or_resume_group(
                    task_id,
                    self._active_tasks,
                    self._composites,
                    (TaskStatus.RECEIVED, TaskStatus.IN_PROGRESS),
                    self._try_pause,
                )
            except KeyError:
                self._node.get_logger().error(f"Tried to pause a task with ID {task_id}, but the task is not active.")
                response.success = False
                continue

            if any_failure:
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
                any_failure = _pause_or_resume_group(
                    task_id, self._active_tasks, self._composites, (TaskStatus.PAUSED,), self._try_resume
                )
            except KeyError:
                self._node.get_logger().error(f"Tried to resume a task with ID {task_id}, but the task is not active.")
                response.success = False
                continue

            if any_failure:
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
