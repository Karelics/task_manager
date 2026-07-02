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
import json
import time
import uuid
from threading import Lock
from typing import Callable, List, Optional, Tuple

# ROS
import rclpy
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.node import Node
from rclpy.publisher import Publisher

# Task Manager messages
from task_manager_msgs.action import ExecuteTask, PerformInParallel
from task_manager_msgs.msg import SubtaskResult, TaskDoneResult, TaskStatus

# Task Manager
from task_manager.task_client import TaskClient
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
        results_pub: Publisher,
        mutex: Lock,
        start_task_cb: Callable[[ExecuteTask.Goal], Tuple[Optional[TaskClient], str]],
    ) -> None:
        """
        :param node: Reference to a parent node
        :param topic: Topic name of the action server
        :param known_tasks: Dictionary of known tasks
        :param results_pub: Publisher for publishing task results
        :param mutex: Mutex for synchronizing access to shared resources
        :param start_task_cb: Callback function to start a new task
        """
        self._node = node
        self._parallel_executor_server = ActionServer(
            self._node,
            PerformInParallel,
            action_name=topic,
            execute_callback=self.perform_in_parallel_cb,
            cancel_callback=self._cancel_cb,
        )
        self._timeout = self._node.declare_parameter("parallel_executor_task.cancel_timeout", 5.0).value

        self._mutex = mutex
        self.results_pub = results_pub
        self._start_task_cb = start_task_cb

    def _cancel_cb(self, _goal_handle: ServerGoalHandle) -> CancelResponse:
        self._node.get_logger().info("Cancel request received for parallel executor")
        return CancelResponse.ACCEPT

    def perform_in_parallel_cb(self, goal_handle: ServerGoalHandle) -> PerformInParallel.Result:
        """Parses, starts and waits for the subtasks to complete.

        :param goal_handle: Handle of the action goal
        :return: Result of the action
        """
        if len(goal_handle.request.subtasks) == 0:
            self._node.get_logger().warning("No subtasks provided for parallel execution")
            goal_handle.succeed()
            return PerformInParallel.Result(message="WARNING: No subtasks provided for parallel execution")

        subtasks: List[ParallelTask] = []
        try:
            subtasks = self._gather_and_try_to_run_subtasks(goal_handle, subtasks)
            self._wait_actions_done(goal_handle, subtasks)

        # TODO: Where can the TimeoutError and ValueError come from?
        except (TimeoutError, ValueError, RuntimeError) as e:
            # Some task failed to start or finish
            self._node.get_logger().error(f"Error while executing parallel actions: {repr(e)}")

        if not rclpy.ok():
            self._node.get_logger().error("Parallel execution task failed due to rclpy not being ok.")
            # Have nothing to do here if rclpy fails
            goal_handle.abort()
            return PerformInParallel.Result()

        result = self._cancel_remaining_tasks_and_get_results(goal_handle, subtasks)
        self._node.get_logger().info(f"Parallel execution task finished with result: {result}")
        return result

    def _gather_and_try_to_run_subtasks(
        self, goal_handle: ServerGoalHandle, subtasks: List[ParallelTask]
    ) -> List[ParallelTask]:
        """Creates subtasks one by one and try to start them.

        Each subtask that started successfully is put into the list of subtasks.

        :param goal_handle: Handle of this execute in parallel goal
        :param subtasks: Reference to the list of actions. This list is filled by this method by the
        all actions that successfully launched
        :return: The same reference to subtasks list
        :raises RuntimeError: If some task fails to start
        """
        for subtask in goal_handle.request.subtasks:
            # TODO: DUPLICATE stuff
            if subtask.task_id == "":
                subtask.task_id = str(uuid.uuid4())

            response = ExecuteTask.Result()
            response.task_id = subtask.task_id

            parent_id = str(uuid.UUID(bytes=bytes(goal_handle.goal_id.uuid)))
            source = f"ParallelExecutor-{parent_id}"
            goal = ExecuteTask.Goal(
                task_id=subtask.task_id, task_name=subtask.task_name, task_data=subtask.task_data, source=source
            )

            # Mutex lock required, since we need to be sure that the previous blocking task has
            # truly finished before we try to start another one from another thread.
            self._node.get_logger().info(f"Starting task {subtask.task_name}")
            with self._mutex:
                task_client, error_code = self._start_task_cb(goal)

            if error_code:
                response.task_status = TaskStatus.ERROR
                response.error_code = error_code
                response.task_result = json.dumps({})

                # Normally the done result is published automatically when task_client has finished. Now we are not
                # creating the task_client at all, since the task has failed while trying to start it.
                self.results_pub.publish(
                    TaskDoneResult(
                        task_id=subtask.task_id,
                        task_name=subtask.task_name,
                        task_status=response.task_status,
                        error_code=response.error_code,
                        source=source,
                        task_result=response.task_result,
                    )
                )
                raise RuntimeError(
                    f"Failed to start task {subtask.task_name} with id {subtask.task_id}. Error code: {error_code}"
                )

            new_task = ParallelTask(task_client, self._timeout, self._node.get_logger())
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
                self._node.get_logger().info("Cancel requested for parallel task.")
                goal_handle.canceled()
                return

            if not goal_handle.is_active:
                self._node.get_logger().warning("Goal handle is not active, aborting parallel tasks.")
                return

            for action in subtasks:
                if not action.active:
                    self._node.get_logger().info(
                        f"Action '{action.name}' finished. The whole parallel action will be cancelled"
                    )
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
        any_done = False
        any_failed = False

        # Try to cancel all tasks
        for task in subtasks:
            task.cancel_async()

        cancel_timeout_start = time.time()
        while rclpy.ok() and (time.time() - cancel_timeout_start < self._timeout):
            if all(task.has_canceled() for task in subtasks):
                break
            time.sleep(0.1)

        # Note: Aborted task can have both ERROR or IN_PROGRESS statuses.
        # IN_PROGRESS status will be preserved if cancelling this action has been failed
        for task in subtasks:
            task_result = task.get_result()
            result.results.append(task_result)
            any_done = any_done or (task_result.task_status == TaskStatus.DONE)
            any_failed = (
                any_failed
                or task_result.task_status == TaskStatus.ERROR
                or (task_result.task_status == TaskStatus.IN_PROGRESS and task.require_finish_on_parallel_cancel())
            )

        # Since our tasks are started in the same order as they provided in the goal handle, here our
        # list of results contains the results of N first tasks that were able to start. If the length of the result
        # list is smaller than list of SubtaskGoals -> this means that the next task has failed to start and other
        # tasks after it didn't even try to be started.
        result_len = len(result.results)
        if result_len < len(goal_handle.request.subtasks):
            result.results.append(
                SubtaskResult(
                    task_id=goal_handle.request.subtasks[result_len].task_id,
                    task_name=goal_handle.request.subtasks[result_len].task_name,
                    skipped=False,
                    task_status=TaskStatus.ERROR,
                )
            )
            any_failed = True  # Set this flag unconditionally since we have at least one task failed to start

            # Other tasks (if any left) were not even tried to start, so they are CANCELLED and skipped
            result.results.extend(
                SubtaskResult(
                    task_id=action.task_id,
                    task_name=action.task_name,
                    skipped=True,
                    task_status=TaskStatus.CANCELED,
                )
                for action in goal_handle.request.subtasks[result_len + 1 :]
            )

        if goal_handle.is_active:
            # Goal is active here if some task was done or failed while this parallel task is still active,
            # we consider this task to be aborted if any task has failed, then if any task done (while others are still
            # active) we consider this as success. And finally: if no tasks failed and no task done - then we assume
            # this parallel task to be cancelled.
            if any_failed:
                goal_handle.abort()
            elif any_done:
                goal_handle.succeed()
            else:
                # Note: according to the logic in the _wait_actions_done this case should never happen - we got result
                # that at least some task is no more active, but it is neither DONE nor FAILED.
                self._node.get_logger().warning(
                    "ParallelExecutor goal_handle is active, but there is no task with done or failed status"
                )
                goal_handle.canceled()

        return result

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        return TaskSpecs(
            task_name="system/perform_in_parallel",
            blocking=False,
            cancel_on_stop=True,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=True,
            msg_interface=PerformInParallel,
            task_server_type=TaskServerType.ACTION,
        )
