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

import uuid
from typing import Callable

# ROS
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

# Karelics messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.action import Mission as MissionAction
from task_manager_msgs.msg import SubtaskResult, TaskStatus

# Current package
from task_manager.task_specs import TaskServerType, TaskSpecs


class Mission:
    """Implements the Mission task, which is able to compose multiple existing tasks."""

    def __init__(
        self,
        node: Node,
        task_topic_prefix: str,
        execute_task_cb: Callable[[ExecuteTask.Goal, ServerGoalHandle], ExecuteTask.Result],
    ):
        """
        :param node: ROS Node
        :param task_topic_prefix: Action topic prefix of the task
        :param execute_task_cb: Callback to execute a single task
        """
        self.execute_task_cb = execute_task_cb
        self._task_topic_prefix = task_topic_prefix
        ActionServer(
            node=node,
            action_type=MissionAction,
            action_name=f"{task_topic_prefix}/system/mission",
            execute_callback=self.execute_cb,
            cancel_callback=self._cancel_cb,
            callback_group=ReentrantCallbackGroup(),
        )

    def execute_cb(self, goal_handle: ServerGoalHandle) -> MissionAction.Result:
        """Execution callback of the Mission Action Server, that executes subtasks one by one."""
        # Generate task IDs for all the tasks and append results, so that if something goes wrong
        # with subtask execution, we still have a result for all the requested subtasks
        request = goal_handle.request
        result = MissionAction.Result()
        for subtask in request.subtasks:
            if subtask.task_id == "":
                subtask.task_id = str(uuid.uuid4())
            result.mission_results.append(
                SubtaskResult(
                    task=subtask.task,
                    task_id=subtask.task_id,
                    status=TaskStatus.RECEIVED,
                )
            )

        for subtask, mission_result in zip(request.subtasks, result.mission_results):
            goal = ExecuteTask.Goal(
                task_id=subtask.task_id, task=subtask.task, task_data=subtask.data, source="Mission"
            )
            subtask_result = self.execute_task_cb(goal, goal_handle)
            mission_result.status = subtask_result.status
            mission_result.skipped = False

            if subtask_result.status != TaskStatus.DONE:
                if subtask.allow_skipping:
                    mission_result.skipped = True
                    continue

                if subtask_result.status == TaskStatus.CANCELED and goal_handle.is_cancel_requested:
                    # Need to also check if the cancel was requested. If the goal was cancelled
                    # through a system task, we cannot set the status to be cancelled and must abort instead.
                    goal_handle.canceled()
                else:  # Could be ERROR or IN_PROGRESS if the goal couldn't be cancelled
                    goal_handle.abort()
                return result

        goal_handle.succeed()
        return result

    def get_task_specs(self) -> TaskSpecs:
        """Returns TaskSpecs object that describes the task properties."""
        return TaskSpecs(
            task_name="system/mission",
            blocking=False,
            cancel_on_stop=False,
            topic=f"_{self._task_topic_prefix}/system/mission",
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=MissionAction,
            task_server_type=TaskServerType.ACTION,
        )

    @staticmethod
    def _cancel_cb(_goal_handle):
        return CancelResponse.ACCEPT
