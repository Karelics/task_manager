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
from typing import Callable, Dict, List

# ROS
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

# Task Manager messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.action import Mission as MissionAction
from task_manager_msgs.msg import SubtaskResult, TaskStatus

# Task Manager
from task_manager.task_specs import TaskServerType, TaskSpecs
from task_manager.tasks.system_tasks import ActiveChildrenTracker, SystemTask


class Mission(SystemTask, ActiveChildrenTracker):
    """Implements the Mission task, which is able to compose multiple existing tasks."""

    def __init__(
        self,
        node: Node,
        action_name: str,
        execute_task_cb: Callable[[ExecuteTask.Goal, ServerGoalHandle], ExecuteTask.Result],
    ):
        """
        :param node: ROS Node
        :param action_name: Action topic of the mission action server
        :param execute_task_cb: Callback to execute a single task
        """
        self.execute_task_cb = execute_task_cb
        # ROS action goal_id (as bytes) of each running mission -> task_id of its currently running subtask.
        self._goal_id_to_subtask_id: Dict[bytes, str] = {}
        ActionServer(
            node=node,
            action_type=MissionAction,
            action_name=action_name,
            execute_callback=self.execute_cb,
            cancel_callback=self._cancel_cb,
            callback_group=ReentrantCallbackGroup(),
        )

    def get_active_children(self, goal_id: bytes) -> List[str]:
        """Returns the mission's active children i.e. its single currently-running subtask or an empty list if that
        mission subtask isn't running (or isn't known)."""
        current = self._goal_id_to_subtask_id.get(goal_id)
        return [current] if current is not None else []

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
                    task_name=subtask.task_name,
                    task_id=subtask.task_id,
                    task_status=TaskStatus.RECEIVED,
                )
            )

        goal_id = bytes(goal_handle.goal_id.uuid)
        try:
            for subtask, mission_result in zip(request.subtasks, result.mission_results):
                if goal_handle.is_cancel_requested:
                    # A cancel/pause arrived before this subtask could be dispatched - don't start it
                    # just to immediately tear it down again.
                    goal_handle.canceled()
                    return result
                self._goal_id_to_subtask_id[goal_id] = subtask.task_id
                goal = ExecuteTask.Goal(
                    task_id=subtask.task_id, task_name=subtask.task_name, task_data=subtask.task_data, source="Mission"
                )
                subtask_result = self.execute_task_cb(goal, goal_handle)
                mission_result.task_status = subtask_result.task_status
                mission_result.skipped = False

                if subtask_result.task_status != TaskStatus.DONE:
                    if subtask.allow_skipping and not goal_handle.is_cancel_requested:
                        mission_result.skipped = True
                        continue

                    # If the subtask has been cancelled together with the mission, we cancel the mission
                    # If the subtask has been cancelled/failed/indefinitely in progress, we abort the mission
                    # If the mission has been cancelled but the subtask is failed/indefinitely in progress,
                    # we abort the mission
                    if subtask_result.task_status == TaskStatus.CANCELED and goal_handle.is_cancel_requested:
                        goal_handle.canceled()
                    else:
                        goal_handle.abort()
                    return result

            goal_handle.succeed()
            return result
        finally:
            self._goal_id_to_subtask_id.pop(goal_id, None)

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        """Returns TaskSpecs object that describes the task properties."""
        return TaskSpecs(
            task_name="system/mission",
            blocking=False,
            cancel_on_stop=True,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=MissionAction,
            task_server_type=TaskServerType.ACTION,
        )

    @staticmethod
    def _cancel_cb(_goal_handle):
        return CancelResponse.ACCEPT
