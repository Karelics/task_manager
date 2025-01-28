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

# ROS
from rclpy.action.server import ActionServer, CancelResponse, ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

# Thirdparty
from rosbridge_library.internal.message_conversion import extract_values, populate_instance

# Task Manager messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.task_specs import TaskSpecs

# pylint: disable=too-few-public-methods
# Class structure makes sense in this case


class TaskActionServer:
    """Provides Action Server interface for tasks to easily call them for example from the Command Line."""

    def __init__(self, node: Node, task_specs: TaskSpecs, task_topic_prefix: str, execute_task_cb: callable):
        """

        :param node: ROS Node
        :param task_specs: General task info
        :param task_topic_prefix: Action topic prefix for the task
        :param execute_task_cb: Callback to execute a single task
        """
        self._execute_task_cb = execute_task_cb
        self.task_specs = task_specs
        ActionServer(
            node=node,
            action_type=task_specs.msg_interface,
            action_name=f"{task_topic_prefix}/{task_specs.task_name}",
            execute_callback=self._execute_cb,
            cancel_callback=self._cancel_cb,
            callback_group=ReentrantCallbackGroup(),
        )
        self._node = node  # Keep reference for logging purposes

    def _execute_cb(self, goal_handle: ServerGoalHandle):
        request = goal_handle.request
        task_data = json.dumps(extract_values(request))
        goal = ExecuteTask.Goal(task_id="", task_name=self.task_specs.task_name, task_data=task_data, source="")
        result = self._execute_task_cb(goal, goal_handle)
        if result.task_status == TaskStatus.DONE:
            goal_handle.succeed()
        elif result.task_status == TaskStatus.CANCELED and goal_handle.is_cancel_requested:
            # Need to also check if the cancel was requested. If the goal was cancelled
            # through a system task, we cannot set the status to be cancelled and must abort instead.
            goal_handle.canceled()
        elif result.task_status == TaskStatus.ERROR:
            self._node.get_logger().error(
                f"Task {self.task_specs.task_name} failed with error code '{result.error_code}'."
            )
            goal_handle.abort()
        else:  # Could be IN_PROGRESS if the goal couldn't be cancelled
            self._node.get_logger().error(
                f"Task {self.task_specs.task_name} failed with unknown status. Could still be in progress."
            )
            goal_handle.abort()

        try:
            return populate_instance(json.loads(result.task_result), self.task_specs.msg_interface.Result())
        except json.decoder.JSONDecodeError as e:
            self._node.get_logger().warning(f"Failed to decode task result: {repr(e)}. Returning empty result.")
            return self.task_specs.msg_interface.Result()

    @staticmethod
    def _cancel_cb(_goal_handle):
        return CancelResponse.ACCEPT
