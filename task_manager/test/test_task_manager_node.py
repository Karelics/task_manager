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

# ROS
import rclpy
from rclpy import Parameter

# ROS messages
from example_interfaces.action import Fibonacci
from example_interfaces.srv import AddTwoInts

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.active_tasks import ActiveTasks
from task_manager.task_manager_node import TaskManager
from task_manager.task_specs import TaskServerType, TaskSpecs


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


class TestDeclareTasks(unittest.TestCase):
    """Unit tests for TaskManager.declare_tasks."""

    def setUp(self) -> None:
        rclpy.init()
        self.addCleanup(rclpy.try_shutdown)

    def _create_task_manager(self, parameters: list[Parameter] | None = None) -> TaskManager:
        task_manager = TaskManager(active_tasks=ActiveTasks(), parameter_overrides=parameters)
        self.addCleanup(task_manager.destroy_node)
        return task_manager

    @staticmethod
    def _task_parameters(task: str, **values) -> list[Parameter]:
        return [Parameter(f"{task}.{name}", value=value) for name, value in values.items()]

    def test_no_tasks_declared_logs_an_error_and_declares_nothing(self):
        """Without a 'tasks' parameter nothing is declared, and the missing configuration is reported as an error."""
        task_manager = self._create_task_manager()

        with patch.object(task_manager, "get_logger") as get_logger:
            task_manager.declare_tasks()

        self.assertEqual(task_manager.known_tasks, {})
        get_logger.return_value.error.assert_called_once()

    def test_declared_tasks_get_their_specs_from_the_parameters(self):
        """Every task parameter ends up in the matching field of the task's specs, and the task server type is detected
        from the message interface.

        The values differ between the two tasks, so that a parameter read into the wrong field is noticed.
        """
        task_manager = self._create_task_manager(
            [Parameter("tasks", value=["fibonacci", "add_two_ints"])]
            + self._task_parameters(
                "fibonacci",
                task_name="fibonacci",
                topic="/fibonacci_topic",
                msg_interface="example_interfaces.action.Fibonacci",
                blocking=True,
                cancel_on_stop=False,
                cancel_reported_as_success=False,
                reentrant=True,
                cancel_timeout=3.5,
                require_finish_on_parallel_cancel=False,
                result_concat_fields=["sequence"],
            )
            + self._task_parameters(
                "add_two_ints",
                task_name="add_two_ints",
                topic="/add_two_ints_topic",
                msg_interface="example_interfaces.srv.AddTwoInts",
                blocking=False,
                cancel_on_stop=True,
                cancel_reported_as_success=True,
                reentrant=False,
                cancel_timeout=2.5,
                service_success_field="sum",
            )
        )

        task_manager.declare_tasks()

        self.assertEqual(
            task_manager.known_tasks,
            {
                "fibonacci": TaskSpecs(
                    task_name="fibonacci",
                    topic="/fibonacci_topic",
                    msg_interface=Fibonacci,
                    task_server_type=TaskServerType.ACTION,
                    blocking=True,
                    cancel_on_stop=False,
                    cancel_reported_as_success=False,
                    reentrant=True,
                    cancel_timeout=3.5,
                    require_finish_on_parallel_cancel=False,
                    result_concat_fields=["sequence"],
                ),
                "add_two_ints": TaskSpecs(
                    task_name="add_two_ints",
                    topic="/add_two_ints_topic",
                    msg_interface=AddTwoInts,
                    task_server_type=TaskServerType.SERVICE,
                    blocking=False,
                    cancel_on_stop=True,
                    cancel_reported_as_success=True,
                    reentrant=False,
                    cancel_timeout=2.5,
                    service_success_field="sum",
                ),
            },
        )

    def test_optional_task_parameters_have_defaults(self):
        """A task that only sets the required parameters gets the documented defaults.

        Its cancel timeout comes from the 'default_cancel_timeout' parameter.
        """
        task_manager = self._create_task_manager(
            [Parameter("tasks", value=["fibonacci"]), Parameter("default_cancel_timeout", value=7.0)]
            + self._task_parameters(
                "fibonacci",
                task_name="fibonacci",
                topic="/fibonacci_topic",
                msg_interface="example_interfaces.action.Fibonacci",
                blocking=False,
                cancel_on_stop=False,
            )
        )

        task_manager.declare_tasks()

        self.assertEqual(
            task_manager.known_tasks,
            {
                "fibonacci": TaskSpecs(
                    task_name="fibonacci",
                    topic="/fibonacci_topic",
                    msg_interface=Fibonacci,
                    task_server_type=TaskServerType.ACTION,
                    blocking=False,
                    cancel_on_stop=False,
                    cancel_reported_as_success=False,
                    reentrant=False,
                    service_success_field="",
                    cancel_timeout=7.0,
                    require_finish_on_parallel_cancel=True,
                    result_concat_fields=[],
                )
            },
        )

    def test_service_success_field_missing_from_the_response_exits(self):
        """A service_success_field that does not exist in the service Response is a configuration error: it is reported
        and the node exits, instead of starting with a task that can never report success."""
        task_manager = self._create_task_manager(
            [Parameter("tasks", value=["add_two_ints"])]
            + self._task_parameters(
                "add_two_ints",
                task_name="add_two_ints",
                topic="/add_two_ints",
                msg_interface="example_interfaces.srv.AddTwoInts",
                blocking=False,
                cancel_on_stop=False,
                service_success_field="no_such_field",
            )
        )

        with patch.object(task_manager, "get_logger") as get_logger:
            with self.assertRaises(SystemExit):
                task_manager.declare_tasks()

        get_logger.return_value.error.assert_called_once()
        self.assertEqual(task_manager.known_tasks, {})

    def test_result_concat_field_missing_from_the_result_exits(self):
        """A result_concat_fields entry that does not exist in the action Result is a configuration error: it is
        reported and the node exits, instead of silently never concatenating that field."""
        task_manager = self._create_task_manager(
            [Parameter("tasks", value=["fibonacci"])]
            + self._task_parameters(
                "fibonacci",
                task_name="fibonacci",
                topic="/fibonacci",
                msg_interface="example_interfaces.action.Fibonacci",
                blocking=False,
                cancel_on_stop=False,
                result_concat_fields=["sequence", "no_such_field"],
            )
        )

        with patch.object(task_manager, "get_logger") as get_logger:
            with self.assertRaises(SystemExit):
                task_manager.declare_tasks()

        get_logger.return_value.error.assert_called_once()
        self.assertEqual(task_manager.known_tasks, {})


if __name__ == "__main__":
    unittest.main()
