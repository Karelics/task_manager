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
from unittest.mock import Mock, patch

# ROS messages
from example_interfaces.action import Fibonacci

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.task_manager_node import TaskManager


class TestWaitForTaskFinish(unittest.TestCase):
    """Unit tests for TaskManager._wait_for_task_finish."""

    @patch("task_manager.task_manager_node.rclpy.ok", return_value=True)
    def test_finished_task_returns_status_and_result(self, _mock_ok):
        """A finished task returns its status and its result as JSON."""
        task_client = Mock(goal_done=True)
        task_client.task_details.status = TaskStatus.DONE
        task_client.task_details.result = Fibonacci.Result(sequence=[0, 1])

        status, task_result = TaskManager._wait_for_task_finish(task_client)

        self.assertEqual(status, TaskStatus.DONE)
        self.assertEqual(task_result, '{"sequence": [0, 1]}')

    @patch("task_manager.task_manager_node.rclpy.ok", return_value=False)
    def test_shutdown_while_task_is_running_returns_no_result(self, _mock_ok):
        """If rclpy shuts down while the task is still running, there is no result to report: return the task's status
        with an empty result instead of raising."""
        task_client = Mock(goal_done=False)
        task_client.task_details.status = TaskStatus.IN_PROGRESS
        task_client.task_details.result = None

        status, task_result = TaskManager._wait_for_task_finish(task_client)

        self.assertEqual(status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task_result, "")


if __name__ == "__main__":
    unittest.main()
