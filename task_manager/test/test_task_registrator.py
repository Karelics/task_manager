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
from unittest import mock
from unittest.mock import Mock

# ROS messages
from example_interfaces.action import Fibonacci

# Karelics messages
from task_manager_msgs.action import ExecuteTask

# Current package
from task_manager.active_tasks import ActiveTasks
from task_manager.task_registrator import DuplicateTaskIdException, ROSGoalParsingError, TaskRegistrator, populate_msg
from task_manager.task_specs import TaskServerType, TaskSpecs

# pylint: disable=protected-access, too-many-public-methods


class TestTaskRegistrator(unittest.TestCase):
    """Unit tests for TaskRegistrator.

    Simple tests to verify the core internal functions and error handling of the TaskRegistrator. Leaving the more
    complex end-to-end procedures to be verified in integration tests.
    """

    def setUp(self):
        self.patcher = mock.patch(
            "task_manager.task_registrator.ActionTaskClient.start_task_async", return_value=Mock()
        )
        self.patcher.start()

        self.active_tasks = ActiveTasks()
        self.task_registrator = TaskRegistrator(node=Mock(), active_tasks=self.active_tasks)
        self.task_registrator._action_clients = {"fibonacci": ""}  # To avoid creating a new client
        self.task_specs = TaskSpecs(
            task_name="fibonacci", topic="", msg_interface=Fibonacci, task_server_type=TaskServerType.ACTION
        )

    def tearDown(self):
        self.patcher.stop()

    def test_task_start_happy_flow(self):
        """Happy flow of starting a new task."""
        request = ExecuteTask.Goal(task_id="123", task_name="fibonacci", task_data='{"order": 5}', source="")
        task_client = self.task_registrator.start_new_task(request, self.task_specs)
        self.assertIn(task_client, self.active_tasks.get_active_tasks())

    def test_start_new_task_duplicate_task_id(self) -> None:
        """Starting a new task with already existing task_id raises an exception."""
        start_task_request = ExecuteTask.Goal(task_id="123", task_name="fibonacci", source="", task_data='{"order": 5}')
        self.task_registrator.start_new_task(start_task_request, self.task_specs)

        with self.assertRaises(DuplicateTaskIdException):
            self.task_registrator.start_new_task(start_task_request, self.task_specs)

    def test_populate_msg_with_invalid_data(self):
        """Checks that parsing invalid task_data (json-formatted ROS Msg) is handled correctly."""
        test_cases = (
            {"case": "Non-existent field", "task_data": '{"non-existing": "value"}'},
            {"case": "Field-type-mismatch", "task_data": '{"order": "str_1"}'},
        )

        for test in test_cases:
            with self.subTest(test["case"]):
                with self.assertRaises(ROSGoalParsingError) as context:
                    populate_msg(test["task_data"], Fibonacci.Goal())
                    self.assertTrue("Unable to parse task data" in context.exception)

    def test_populate_msg_with_extra_data_fields(self):
        """Check that extra fields in task_data are ignored."""
        result = populate_msg(task_data='{"order": 1, "extra_field": 2}', msg_interface=Fibonacci.Goal())
        self.assertEqual(type(result), type(Fibonacci.Goal()))
        self.assertEqual(result.order, 1)


if __name__ == "__main__":
    unittest.main()
