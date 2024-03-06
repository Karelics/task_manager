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
from rclpy.node import Node
from rclpy.task import Future

# Thirdparty
from rosbridge_library.internal.message_conversion import extract_values

# ROS messages
from action_msgs.msg import GoalStatus
from example_interfaces.action import Fibonacci

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.task_client import ActionTaskClient, ServiceTaskClient
from task_manager.task_details import TaskDetails

# pylint: disable=protected-access


class TestActionTaskClient(unittest.TestCase):
    """Unit tests for ActionTaskClient.

    Most of the functionality is tested with integration tests
    """

    def setUp(self) -> None:
        self.cb_called = False

    def _done_cb(self, _task_specs, _task_details) -> None:
        self.cb_called = True

    def test_done_cb_normal(self) -> None:
        """Test normal execution of the goal_done_cb."""
        task_client = get_action_task_client("task_1")
        task_client.register_done_callback(self._done_cb)
        goal_future = Future(executor=Mock())
        goal_future._result = Fibonacci.Impl.GetResultService.Response(
            status=GoalStatus.STATUS_SUCCEEDED, result=Fibonacci.Result(sequence=[0, 1])
        )

        task_client._goal_done_cb(goal_future)

        self.assertTrue(self.cb_called)
        self.assertEqual(task_client.task_details.status, TaskStatus.DONE)

        # Getting result.sequence produces weird output: "array('i', [0, 1])", while we expect "[0, 1]"
        # Seems to be a known issue https://github.com/ros2/demos/issues/388
        # As a workaround, use extract_values-function to get the result in json format
        result = extract_values(task_client.task_details.result)
        self.assertEqual(result, {"sequence": [0, 1]}, msg=str(task_client.task_details.result))

    def test_done_cb_bad_status(self) -> None:
        """Test goal_done_cb when the goal status is not valid."""
        task_client = get_action_task_client("task_1")
        task_client.register_done_callback(self._done_cb)
        goal_future = Future(executor=Mock())
        goal_future._result = Fibonacci.Impl.GetResultService.Response(status=-1, result=Fibonacci.Result())

        task_client._goal_done_cb(goal_future)

        self.assertTrue(self.cb_called)
        self.assertEqual(task_client.task_details.status, TaskStatus.ERROR)

        # Getting result.sequence produces weird output: "array('i', [0, 1])", while we expect "[0, 1]"
        # Seems to be a known issue https://github.com/ros2/demos/issues/388
        # As a workaround, use extract_values-function to get the result in json format
        result = extract_values(task_client.task_details.result)
        self.assertEqual(result, {"sequence": []}, msg=str(task_client.task_details.result))


class ServiceTaskClientUnittests(unittest.TestCase):
    """Unittests for ServiceTaskClient.

    Most of the functionality is tested with integration tests.
    """

    def test_service_success_field(self):
        """Task's service_success_field sets the final task status correctly."""
        task_client = ServiceTaskClient(
            node=Mock(), task_details=Mock(), task_specs=Mock(service_success_field="success"), service_clients={}
        )

        mock_future = Mock()
        mock_future.result.return_value = Mock(success=False)
        task_client._done_callback(future=mock_future)
        self.assertEqual(task_client.task_details.status, TaskStatus.ERROR)


def get_action_task_client(
    task_name: str,
) -> ActionTaskClient:
    """Initializes and returns a ActionTaskClient with minimal info for testing purposes."""
    task_details = TaskDetails(
        task_id="1",
        source="CLOUD",
        status=TaskStatus.RECEIVED,
    )
    return ActionTaskClient(
        Mock(spec=Node), task_details, task_specs=Mock(task_name=task_name), action_clients={task_name: Mock()}
    )


if __name__ == "__main__":
    unittest.main()
