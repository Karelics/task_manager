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

from typing import Callable, Dict, List

# ROS
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

# Task Manager messages
from task_manager_msgs.action import Container as ContainerAction
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.own_thread import run_in_own_thread
from task_manager.task_specs import TaskServerType, TaskSpecs
from task_manager.tasks.mission import build_subtask_results
from task_manager.tasks.system_tasks import SystemTask


class Container(SystemTask):
    """Implements the Container task: runs its subtasks sequentially and is paused/resumed as a single unit.

    A container is a pause leaf - pause/resume never descends into its subtasks. Pausing it goes through its own
    ActionTaskClient, which cancels the container goal (cancelling the currently running subtask with it), and resuming
    re-sends the same goal, so the container always restarts from its first subtask. Used for groups of steps that
    depend on the previous step having just been completed, e.g. navigate somewhere and then perform a task there.
    """

    def __init__(
        self,
        node: Node,
        action_name: str,
        execute_task_cb: Callable[[ExecuteTask.Goal, ServerGoalHandle], ExecuteTask.Result],
    ):
        """
        :param node: ROS Node
        :param action_name: Action topic of the container action server
        :param execute_task_cb: Callback to execute a single task
        """
        self.execute_task_cb = execute_task_cb
        # ROS action goal_id (as bytes) of each running container -> task_id of its currently running subtask.
        self._current_child: Dict[bytes, str] = {}
        ActionServer(
            node=node,
            action_type=ContainerAction,
            action_name=action_name,
            execute_callback=run_in_own_thread(self.execute_cb),
            cancel_callback=self._cancel_cb,
            callback_group=ReentrantCallbackGroup(),
        )

    def get_active_children(self, goal_id: bytes) -> List[str]:
        """Returns the container's currently running subtask, or an empty list if none is running (or the goal is
        unknown).

        Only used to redirect a pause/resume request for a subtask up to its container.
        """
        current = self._current_child.get(goal_id)
        return [current] if current is not None else []

    def execute_cb(self, goal_handle: ServerGoalHandle) -> ContainerAction.Result:
        """Execution callback of the Container Action Server, that executes subtasks one by one."""
        request = goal_handle.request
        result = ContainerAction.Result(results=build_subtask_results(request.subtasks))

        goal_id = bytes(goal_handle.goal_id.uuid)
        try:
            for subtask, subtask_result in zip(request.subtasks, result.results):
                if goal_handle.is_cancel_requested:
                    goal_handle.canceled()
                    return result

                self._current_child[goal_id] = subtask.task_id
                goal = ExecuteTask.Goal(
                    task_id=subtask.task_id,
                    task_name=subtask.task_name,
                    task_data=subtask.task_data,
                    source="Container",
                )
                subtask_result.task_status = self.execute_task_cb(goal, goal_handle).task_status
                subtask_result.skipped = False

                if goal_handle.is_cancel_requested:
                    # Cancelled or paused: always end as CANCELED, whatever the subtask reported (a subtask's server
                    # may abort instead of cancel). Aborting here would make the container's ActionTaskClient treat
                    # a pause as a real finish, aborting any mission this container runs in.
                    goal_handle.canceled()
                    return result

                if subtask_result.task_status != TaskStatus.DONE:
                    if not subtask.allow_skipping:
                        goal_handle.abort()
                        return result
                    subtask_result.skipped = True

            goal_handle.succeed()
            return result
        finally:
            self._current_child.pop(goal_id, None)

    @staticmethod
    def get_task_specs(topic: str, cancel_timeout: float = 5.0) -> TaskSpecs:
        """Returns TaskSpecs object that describes the task properties.

        :param topic: Action topic of the container action server
        :param cancel_timeout: How long pausing/cancelling the container may take. Must exceed the cancel time of any
            of its subtasks, as the container only finishes cancelling once its running subtask has.
        """
        return TaskSpecs(
            task_name="system/container",
            blocking=False,
            cancel_on_stop=True,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=True,
            msg_interface=ContainerAction,
            task_server_type=TaskServerType.ACTION,
            cancel_timeout=cancel_timeout,
        )

    @staticmethod
    def _cancel_cb(_goal_handle):
        return CancelResponse.ACCEPT
