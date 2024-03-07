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
from typing import Callable, Dict
from unittest.mock import Mock

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import CancelTaskFailedError, TaskClient
from task_manager.task_details import TaskDetails
from task_manager.task_specs import TaskSpecs

# pylint: disable=protected-access


class TestActiveTasks(unittest.TestCase):
    """Unit tests for ActiveTasks."""

    def setUp(self) -> None:
        self.cli_1 = Mock(spec=TaskClient)
        self.cli_1.task_specs = TaskSpecs(
            task_name="test_task_1",
            topic=Mock(),
            msg_interface=Mock(),
            task_server_type=Mock(),
            blocking=False,
            cancel_on_stop=True,
        )
        self.cli_1.task_details = TaskDetails(
            task_id="task_1",
            source="CLOUD",
            status=TaskStatus.IN_PROGRESS,
        )

        self.cli_2 = Mock(spec=TaskClient)

        self.cli_2.task_specs = TaskSpecs(
            task_name="test_task_2",
            topic=Mock(),
            msg_interface=Mock(),
            task_server_type=Mock(),
            blocking=True,
            cancel_on_stop=True,
        )
        self.cli_2.task_details = TaskDetails(
            task_id="task_2",
            source="CLOUD",
            status=TaskStatus.IN_PROGRESS,
        )

        self.cli_3 = Mock(spec=TaskClient)
        self.cli_3.task_specs = TaskSpecs(
            task_name="test_task_3",
            topic=Mock(),
            msg_interface=Mock(),
            task_server_type=Mock(),
            blocking=False,
            cancel_on_stop=False,
        )
        self.cli_3.task_details = TaskDetails(
            task_id="task_3",
            source="CLOUD",
            status=TaskStatus.IN_PROGRESS,
        )

        self.changed_cb = Mock(spec=Callable[[Dict[str, TaskClient]], None])
        self.active_tasks = ActiveTasks(self.changed_cb)
        self.active_tasks._active_tasks = {
            self.cli_1.task_details.task_id: self.cli_1,
            self.cli_2.task_details.task_id: self.cli_2,
            self.cli_3.task_details.task_id: self.cli_3,
        }

        self.changed_cb.reset_mock()

    def test_add(self):
        """Test add method."""
        new_cli = Mock(spec=TaskClient)
        new_cli.task_specs = TaskSpecs(
            task_name="test_task_4",
            topic=Mock(),
            msg_interface=Mock(),
            task_server_type=Mock(),
            blocking=True,
            cancel_on_stop=True,
        )
        new_cli.task_details = TaskDetails(
            task_id="task_4",
            source="CLOUD",
            status=TaskStatus.RECEIVED,
        )
        self.active_tasks.add(new_cli)

        self.assertIn(new_cli, self.active_tasks._active_tasks.values())
        self.changed_cb.assert_called_once()
        new_cli.register_done_callback.assert_called_once()

    def test_delete(self):
        """Test delete method."""
        self.active_tasks._delete(self.cli_1.task_details.task_id)
        self.assertNotIn(self.cli_1, self.active_tasks._active_tasks.values())
        self.changed_cb.assert_called_once()

    def test_clear_all(self):
        """Test clear all method."""
        self.active_tasks.clear_all()
        self.assertEqual(self.active_tasks._active_tasks, {})
        self.changed_cb.assert_called_once()

    def test_get_active_task(self):
        """Test fetching active task by task task name."""
        tasks = self.active_tasks.get_active_tasks_by_name(self.cli_1.task_specs.task_name)
        self.assertEqual(tasks[0], self.cli_1)
        self.assertIn(self.cli_1, self.active_tasks._active_tasks.values())

    def test_get_active_task_not_found(self):
        """Test fetching active task by task name when the task is not found."""
        tasks = self.active_tasks.get_active_tasks_by_name("non_existing_name")
        self.assertTrue(not tasks)

    def test_get_blocking_task(self):
        """Test fetching current blocking task."""
        task = self.active_tasks.get_blocking_task()
        self.assertEqual(task, self.cli_2)
        self.assertIn(self.cli_2, self.active_tasks._active_tasks.values())

    def test_get_blocking_task_not_found(self):
        """Test fetching current blocking task when none is found."""
        self.cli_2.task_specs.blocking = False
        task = self.active_tasks.get_blocking_task()
        self.assertIsNone(task)

    def test_cancel_tasks_on_stop(self):
        """Test canceling all tasks to be canceled on stop."""
        self.active_tasks.cancel_tasks_on_stop()
        self.cli_1.cancel_task.assert_called_once()
        self.cli_2.cancel_task.assert_called_once()
        self.cli_3.cancel_task.assert_not_called()

    def test_cancel_tasks_on_stop_fail_on_first_cancel(self):
        """Test canceling all tasks to be canceled on stop when the first cancel fails."""
        self.cli_1.cancel_task.side_effect = CancelTaskFailedError()
        self.assertRaises(CancelTaskFailedError, self.active_tasks.cancel_tasks_on_stop)
        self.cli_1.cancel_task.assert_called_once()
        self.cli_2.cancel_task.assert_called_once()
        self.cli_3.cancel_task.assert_not_called()

    def test_cancel_task(self):
        """Test canceling a task by task id."""
        self.active_tasks.cancel_task(self.cli_1.task_details.task_id)
        self.cli_1.cancel_task.assert_called_once()
        self.assertIn(self.cli_1, self.active_tasks._active_tasks.values())

    def test_get_active_tasks(self):
        """Test getting all the currently active tasks."""
        active_tasks = self.active_tasks.get_active_tasks()
        self.assertIn(self.cli_1, active_tasks)
        self.assertIn(self.cli_2, active_tasks)
        self.assertIn(self.cli_3, active_tasks)


if __name__ == "__main__":
    unittest.main()
