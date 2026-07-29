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
import uuid
from threading import Lock
from typing import Callable, Dict, List, Optional, Tuple

# ROS
import rclpy
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.node import Node

# Task Manager messages
from task_manager_msgs.action import ExecuteTask, PerformInParallel
from task_manager_msgs.msg import SubtaskResult, TaskStatus

# Task Manager
from task_manager.task_client import CancelTaskFailedError, TaskClient
from task_manager.task_specs import TaskServerType, TaskSpecs
from task_manager.tasks.parallel_task import ParallelTask
from task_manager.tasks.system_tasks import SystemTask


# This class wraps an action server which is to be only interfaced via ROS actions.
# pylint: disable=too-few-public-methods
class ParallelTaskExecutor(SystemTask):
    """This task executes a list of tasks in parallel."""

    def __init__(
        self,
        node: Node,
        topic: str,
        prepare_execute_task_result_cb: Callable[[ExecuteTask.Goal], ExecuteTask.Result],
        start_single_task_cb: Callable[[ExecuteTask.Goal, ExecuteTask.Result], Tuple[Optional[TaskClient], str]],
    ) -> None:
        """
        :param node: Reference to a parent node
        :param topic: Topic name of the action server
        :param prepare_execute_task_result_cb: Callback to prepare the ExecuteTask.Result message with the task_id
        :param start_single_task_cb: Callback to start a single task and return the TaskClient and an error code
        """
        self._node = node
        self._logger = node.get_logger().get_child("ParallelTask")
        self._parallel_executor_server = ActionServer(
            self._node,
            PerformInParallel,
            action_name=topic,
            execute_callback=self._execute_cb,
            cancel_callback=self._cancel_cb,
            handle_accepted_callback=self._handle_accepted_cb,
        )
        self._timeout = self._node.declare_parameter("parallel_executor_task.cancel_timeout", 5.0).value

        self._latest_goal_handle: Optional[ServerGoalHandle] = None
        self._execution_lock = Lock()

        self._prepare_execute_task_result_cb = prepare_execute_task_result_cb
        self._start_single_task_cb = start_single_task_cb

        # ROS action goal_id (as bytes) of each running "perform in parallel" invocation -> its subtasks. Keyed by
        # goal_id (shared by the client and server side of the same action call), mirroring Mission's own
        # `_current_subtask_ids` tracking, so that pausing/resuming the group (whether targeted by its own task_id
        # or by one of its members') can find every member currently in flight.
        self._active_subtasks: Dict[bytes, List[ParallelTask]] = {}

    def get_active_children(self, goal_id: bytes) -> List[str]:
        """Satisfies the generic `ActiveChildrenTracker` protocol used by pause/resume (system_tasks.py): returns the
        task_ids of the subtasks of the "perform in parallel" invocation identified by goal_id that are still live (not
        yet finished).

        Empty list if that invocation isn't known or none of its members are still live.
        """
        return [task.task_id for task in self._active_subtasks.get(goal_id, []) if task.active]

    def _execute_cb(self, goal_handle: ServerGoalHandle) -> PerformInParallel.Result:
        """Wraps the perform_in_parallel_cb method to acquire a lock before executing the parallel tasks."""
        with self._execution_lock:
            return self.perform_in_parallel_cb(goal_handle)

    def perform_in_parallel_cb(self, goal_handle: ServerGoalHandle) -> PerformInParallel.Result:
        """Parses, starts and waits for the subtasks to complete.

        :param goal_handle: Handle of the action goal
        :return: Result of the action
        """
        if len(goal_handle.request.subtasks) == 0:
            self._logger.warning("No subtasks provided for parallel execution")
            goal_handle.succeed()
            return PerformInParallel.Result(message="WARNING: No subtasks provided for parallel execution")

        subtasks: List[ParallelTask] = []
        message = ""
        goal_id = bytes(goal_handle.goal_id.uuid)
        # Registered upfront (before any subtask has actually started) with the same list reference that
        # _gather_and_try_to_run_subtasks appends to, so a pause/resume request racing the startup window still
        # sees whichever subtasks have started so far.
        self._active_subtasks[goal_id] = subtasks
        try:
            subtasks = self._gather_and_try_to_run_subtasks(goal_handle, subtasks)
            self._wait_actions_done(goal_handle, subtasks)

        except RuntimeError as e:
            # Some task failed to start or finish
            self._logger.error(f"Error while executing parallel actions: {repr(e)}")

        except PreemptedException:
            self._logger.info("Parallel task was preempted by a new goal")
            message = "Parallel task was preempted by a new goal"

        finally:
            self._active_subtasks.pop(goal_id, None)

        if not rclpy.ok():
            self._logger.error("Parallel execution task failed due to rclpy not being ok.")
            goal_handle.abort()
            return PerformInParallel.Result()

        result = self._cancel_remaining_tasks_and_get_results(goal_handle, subtasks)
        result.message = message
        return result

    def _gather_and_try_to_run_subtasks(
        self, goal_handle: ServerGoalHandle, subtasks: List[ParallelTask]
    ) -> List[ParallelTask]:
        """Creates subtasks one by one and tries to start them.

        Each subtask that started successfully is put into the list of subtasks.

        :param goal_handle: Handle of this execute in parallel goal
        :param subtasks: Reference to the list of actions. This list is filled in this method, by adding
        all actions that successfully launched
        :return: The same reference to subtasks list
        :raises RuntimeError: If some task fails to start
        """
        for subtask in goal_handle.request.subtasks:
            if self._latest_goal_handle != goal_handle:
                raise PreemptedException("Parallel task was preempted")

            parent_id = str(uuid.UUID(bytes=bytes(goal_handle.goal_id.uuid)))
            source = f"ParallelExecutor-{parent_id}"
            goal = ExecuteTask.Goal(
                task_id=subtask.task_id, task_name=subtask.task_name, task_data=subtask.task_data, source=source
            )

            response = self._prepare_execute_task_result_cb(goal)
            task_client, error_code = self._start_single_task_cb(goal, response)
            if error_code or task_client is None:
                raise RuntimeError(
                    f"Failed to start task {subtask.task_name} with id {subtask.task_id}. Error code: {error_code}"
                )

            new_task = ParallelTask(task_client, self._timeout, self._logger)
            new_task.set_source(source)
            subtasks.append(new_task)

        return subtasks

    def _wait_actions_done(self, goal_handle: ServerGoalHandle, subtasks: List[ParallelTask]) -> None:
        """Spins until rclpy not ok or some task is done for some reason.

        :param goal_handle: Handle of the parallel goal
        :param subtasks: Reference to the list of tasks to wait for.
        """
        while rclpy.ok():
            if goal_handle.is_cancel_requested:
                self._logger.info("Cancel requested for parallel task.")
                return

            if not goal_handle.is_active:
                self._logger.warning("Goal handle is not active, aborting parallel tasks.")
                return

            if self._latest_goal_handle != goal_handle:
                raise PreemptedException("Parallel task was preempted")

            for action in subtasks:
                # `has_finished()` (not `active`) is the real completion signal - a paused subtask is neither
                # active nor finished, and must not tear the group down.
                if action.has_finished():
                    self._logger.info(f"Action '{action.name}' finished. The whole parallel action will be cancelled")
                    return

            time.sleep(0.1)

    def _cancel_remaining_tasks_and_get_results(
        self, goal_handle: ServerGoalHandle, subtasks: List[ParallelTask]
    ) -> PerformInParallel.Result:
        """Cancels tasks that have been already launched asynchronously and retrieves their results.

        :param goal_handle: Handle of the parallel goal
        :param subtasks: Reference to the list of tasks to cancel (if they were started).
        :return: The overall PerformInParallel.Result
        """
        result = PerformInParallel.Result()

        self._cancel_tasks_and_wait_for_finish(subtasks)
        any_done, any_failed = self._get_results(subtasks, result)
        any_failed = any_failed or self._set_results_for_unstarted_tasks(goal_handle.request.subtasks, result)

        if any_failed:
            goal_handle.abort()
        elif goal_handle.is_cancel_requested:
            goal_handle.canceled()
        elif any_done:
            goal_handle.succeed()
        else:
            self._logger.info("All tasks were canceled, aborting the goal")
            goal_handle.abort()

        return result

    def _cancel_tasks_and_wait_for_finish(self, subtasks: List[ParallelTask]) -> bool:
        """Cancels tasks that have been already launched asynchronously and waits for them to finish.

        Makes use of `cancel_timeout` parameter to limit the waiting time.

        :param subtasks: Reference to the list of tasks to cancel.
        :return: True if all tasks have been cancelled, False if timeout has been reached
        """
        for task in subtasks:
            try:
                task.cancel_async()
            except CancelTaskFailedError as e:
                self._logger.error(f"Failed to cancel task '{task.name}': {repr(e)}")

        cancel_timeout_start = time.time()
        while rclpy.ok() and (time.time() - cancel_timeout_start < self._timeout):
            if all(task.has_finished() for task in subtasks):
                return True
            time.sleep(0.1)

        # Check if some task is still running but is allowed to continue running after cancelation
        if all(task.has_finished() or not task.require_finish_on_parallel_cancel() for task in subtasks):
            return True
        return False

    def _get_results(self, subtasks: List[ParallelTask], result: PerformInParallel.Result) -> Tuple[bool, bool]:
        """Retrieves results of the tasks and fills the PerformInParallel.Result message.

        :param subtasks: Reference to the list of tasks to get results from.
        :param result: Reference to the PerformInParallel.Result message to fill with results.
        :return: Tuple of two booleans: (any_done, any_failed) representing whether any task was done or failed.
        """
        any_done = False
        any_failed = False

        for task in subtasks:
            task_result = task.get_result()
            result.results.append(task_result)
            any_done = any_done or (task_result.task_status == TaskStatus.DONE)
            any_failed = (
                any_failed
                or task_result.task_status == TaskStatus.ERROR
                or (task_result.task_status == TaskStatus.IN_PROGRESS and task.require_finish_on_parallel_cancel())
            )

        return any_done, any_failed

    def _set_results_for_unstarted_tasks(
        self, requested_tasks: List[SubtaskResult], result: PerformInParallel.Result
    ) -> bool:
        """Sets results for tasks that were not started and fills the PerformInParallel.Result message.

        Len(result.results) tasks have started, which means the next one has failed and will be set to ERROR.
        The rest did not have a chance to get started and will be set to CANCELED and skipped.

        :param requested_tasks: Reference to the list of requested tasks from the original goal request.
        :param result: Reference to the PerformInParallel.Result message to fill with results.
        :return: True if any task was not started, False otherwise.
        """
        result_len = len(result.results)
        if result_len < len(requested_tasks):
            result.results.append(
                SubtaskResult(
                    task_id=requested_tasks[result_len].task_id,
                    task_name=requested_tasks[result_len].task_name,
                    skipped=False,
                    task_status=TaskStatus.ERROR,
                )
            )

            # Other tasks (if any left) were not even tried to start, so they are CANCELLED and skipped
            result.results.extend(
                SubtaskResult(
                    task_id=task.task_id,
                    task_name=task.task_name,
                    skipped=True,
                    task_status=TaskStatus.CANCELED,
                )
                for task in requested_tasks[result_len + 1 :]
            )
            return True
        return False

    def _cancel_cb(self, _goal_handle: ServerGoalHandle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _handle_accepted_cb(self, goal_handle: ServerGoalHandle) -> None:
        self._latest_goal_handle = goal_handle
        goal_handle.execute()

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        return TaskSpecs(
            task_name="system/perform_in_parallel",
            blocking=False,
            cancel_on_stop=True,
            topic=topic,
            cancel_reported_as_success=False,
            cancel_timeout=6.0,
            reentrant=True,
            msg_interface=PerformInParallel,
            task_server_type=TaskServerType.ACTION,
        )


class PreemptedException(Exception):
    """Exception raised when a parallel task is preempted by a new goal."""
