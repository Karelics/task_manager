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
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

# Thirdparty
from rosbridge_library.internal.message_conversion import extract_values, populate_instance

# Task Manager messages
# Karelics messages
from task_manager_msgs.action import ExecuteTask

# Task Manager
# Current package
from task_manager.task_specs import TaskSpecs

# pylint: disable=too-few-public-methods
# Class structure makes sense in this case


class TaskServiceServer:
    """Creates a Service Server for a task to easily call it for example from the command line."""

    def __init__(self, node: Node, task_specs: TaskSpecs, task_topic_prefix: str, execute_task_cb: callable):
        """

        :param node: ROS Node
        :param task_specs: General task info
        :param task_topic_prefix: Service topic prefix for the task
        :param execute_task_cb: Callback to execute a single task
        """
        self._execute_task_cb = execute_task_cb
        self.task_specs = task_specs
        node.create_service(
            task_specs.msg_interface,
            f"{task_topic_prefix}/{task_specs.task_name}",
            callback=self.service_callback,
            callback_group=ReentrantCallbackGroup(),
        )

    def service_callback(self, request, response):
        """Service callback for the task.

        Converts the request into JSON, calls the task execution callback, and return the task result in ROS message
        format.
        """
        task_data = json.dumps(extract_values(request))
        goal = ExecuteTask.Goal(task_id="", task_name=self.task_specs.task_name, task_data=task_data, source="")
        result = self._execute_task_cb(goal)
        return populate_instance(json.loads(result.task_result), response)
