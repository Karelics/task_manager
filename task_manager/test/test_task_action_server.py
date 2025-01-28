#!/usr/bin/env python3

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

import unittest
from unittest.mock import Mock

# ROS
import rclpy
from rclpy.action.server import ServerGoalHandle
from rclpy.node import Node

# ROS messages
from example_interfaces.action import Fibonacci

# Task Manager messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import TaskDoneResult, TaskStatus

# Task Manager
from task_manager.task_specs import TaskServerType, TaskSpecs
from task_manager.tasks.task_action_server import TaskActionServer


# pylint: disable=protected-access

class TestActionTaskClient(unittest.TestCase):
    """Integration tests for ActionTaskClient."""

    def setUp(self) -> None:
        rclpy.init()
        self._node = Node("test_node", namespace="/task_action_server_test")
        self._task_specs = TaskSpecs(
            task_name="test_task",
            topic="/test_task_topic",
            msg_interface=Fibonacci,
            task_server_type=TaskServerType.ACTION,
        )

    def tearDown(self) -> None:
        self._node.destroy_node()
        rclpy.try_shutdown()

    def test_task_action_server(self):
        """Happy flow."""
        task_action_server = TaskActionServer(
            self._node, self._task_specs, "/test_topic_prefix", self._mock_execute_task_cb
        )
        goal_handle = Mock(spec=ServerGoalHandle)
        goal_handle.request = Fibonacci.Goal()

        response = task_action_server._execute_cb(goal_handle)

        self.assertListEqual(list(response.sequence), [0, 1])
        self.assertTrue(goal_handle.succeed.called)

    def test_task_action_server_error(self):
        """Error flow."""
        task_action_server = TaskActionServer(
            self._node, self._task_specs, "/test_topic_prefix", self._mock_execute_task_cb_error
        )
        goal_handle = Mock(spec=ServerGoalHandle)
        goal_handle.request = Fibonacci.Goal()

        response = task_action_server._execute_cb(goal_handle)

        self.assertEqual(list(response.sequence), [])
        self.assertTrue(goal_handle.abort.called)

    @staticmethod
    def _mock_execute_task_cb(_request: ExecuteTask.Goal, _goal_handle: ServerGoalHandle = None) -> ExecuteTask.Result:
        """Mock execute task callback that returns a result."""
        return TaskDoneResult(
            task_id="test_task_id",
            task_name="test_task",
            task_status=TaskStatus.DONE,
            error_code="",
            source="TEST",
            task_result='{"sequence": [0, 1]}',
        )

    @staticmethod
    def _mock_execute_task_cb_error(
        _request: ExecuteTask.Goal, _goal_handle: ServerGoalHandle = None
    ) -> ExecuteTask.Result:
        """Mock execute task callback that returns an error."""
        return TaskDoneResult(
            task_id="test_task_id_error",
            task_name="test_task",
            task_status=TaskStatus.ERROR,
            error_code=ExecuteTask.Result().ERROR_TASK_DATA_PARSING_FAILED,
            source="TEST",
            task_result="{}",
        )
