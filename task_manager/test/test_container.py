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
from unittest.mock import Mock, patch

# Task Manager messages
from task_manager_msgs.action import Container as ContainerAction
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import SubtaskGoal, TaskStatus

# Task Manager
from task_manager.task_specs import TaskServerType
from task_manager.tasks.container import Container

GOAL_ID = bytes([0] * 16)


def make_goal_handle(subtasks, is_cancel_requested=False):
    """Mock ServerGoalHandle for a container goal with the given subtasks."""
    return Mock(
        request=ContainerAction.Goal(subtasks=subtasks),
        goal_id=Mock(uuid=[0] * 16),
        is_cancel_requested=is_cancel_requested,
    )


def subtask(task_id, allow_skipping=False):
    """SubtaskGoal with the given task_id."""
    return SubtaskGoal(task_name=f"task_{task_id}", task_data="{}", task_id=task_id, allow_skipping=allow_skipping)


def result_with(status):
    """ExecuteTask.Result with the given status."""
    return ExecuteTask.Result(task_status=status, task_result="{}")


class ContainerUnittest(unittest.TestCase):
    """Unittests for Container Task."""

    def setUp(self):
        self.patcher = mock.patch("task_manager.tasks.container.ActionServer", return_value=Mock())
        self.patcher.start()

        self.container = Container(node=Mock(), action_name="", execute_task_cb=Mock())

    def tearDown(self):
        self.patcher.stop()

    def test_all_subtasks_succeed(self):
        """Subtasks run in order with source Container and the container succeeds."""
        self.container.execute_task_cb.return_value = result_with(TaskStatus.DONE)
        goal_handle = make_goal_handle([subtask("a"), subtask("b")])

        result = self.container.execute_cb(goal_handle)

        started = [call.args[0] for call in self.container.execute_task_cb.call_args_list]
        self.assertEqual([goal.task_id for goal in started], ["a", "b"])
        self.assertTrue(all(goal.source == "Container" for goal in started))
        self.assertEqual([r.task_status for r in result.results], [TaskStatus.DONE, TaskStatus.DONE])
        goal_handle.succeed.assert_called_once()

    @patch("task_manager.tasks.mission.uuid.uuid4", return_value="123")
    def test_generates_missing_task_ids(self, _mock_uuid):
        """Subtasks without a task_id get a generated one."""
        self.container.execute_task_cb.return_value = result_with(TaskStatus.DONE)
        result = self.container.execute_cb(make_goal_handle([subtask("")]))
        self.assertEqual(result.results[0].task_id, "123")

    def test_failed_subtask_aborts(self):
        """A failed subtask aborts the container and the remaining subtasks are not started."""
        self.container.execute_task_cb.return_value = result_with(TaskStatus.ERROR)
        goal_handle = make_goal_handle([subtask("a"), subtask("b")])

        result = self.container.execute_cb(goal_handle)

        self.assertEqual(self.container.execute_task_cb.call_count, 1)
        self.assertEqual([r.task_status for r in result.results], [TaskStatus.ERROR, TaskStatus.RECEIVED])
        goal_handle.abort.assert_called_once()
        goal_handle.succeed.assert_not_called()

    def test_failed_skippable_subtask_continues(self):
        """A failed subtask with allow_skipping is marked skipped and the container continues."""
        self.container.execute_task_cb.side_effect = [result_with(TaskStatus.ERROR), result_with(TaskStatus.DONE)]
        goal_handle = make_goal_handle([subtask("a", allow_skipping=True), subtask("b")])

        result = self.container.execute_cb(goal_handle)

        self.assertTrue(result.results[0].skipped)
        self.assertFalse(result.results[1].skipped)
        goal_handle.succeed.assert_called_once()

    def test_cancel_before_first_subtask(self):
        """A cancel (or pause) arriving before the first subtask starts doesn't start anything."""
        goal_handle = make_goal_handle([subtask("a")], is_cancel_requested=True)

        self.container.execute_cb(goal_handle)

        self.container.execute_task_cb.assert_not_called()
        goal_handle.canceled.assert_called_once()

    def test_cancel_between_subtasks(self):
        """A cancel (or pause) arriving between two subtasks doesn't start the next one."""
        goal_handle = make_goal_handle([subtask("a"), subtask("b")])

        def finish_then_cancel(_goal, handle):
            handle.is_cancel_requested = True
            return result_with(TaskStatus.DONE)

        self.container.execute_task_cb.side_effect = finish_then_cancel

        self.container.execute_cb(goal_handle)

        self.assertEqual(self.container.execute_task_cb.call_count, 1)
        goal_handle.canceled.assert_called_once()
        goal_handle.succeed.assert_not_called()

    def test_cancel_ends_canceled_even_if_subtask_aborts(self):
        """If the running subtask reports an error instead of CANCELED when the container is cancelled (e.g. its server
        aborts on cancel), the container must still end CANCELED, so that a pause is recognised as a pause."""
        goal_handle = make_goal_handle([subtask("a"), subtask("b")])

        def abort_on_cancel(_goal, handle):
            handle.is_cancel_requested = True
            return result_with(TaskStatus.ERROR)

        self.container.execute_task_cb.side_effect = abort_on_cancel

        self.container.execute_cb(goal_handle)

        goal_handle.canceled.assert_called_once()
        goal_handle.abort.assert_not_called()

    def test_get_active_children_tracks_progress(self):
        """The currently running subtask is reported as the only active child, cleared once the container finishes."""
        seen = []

        def record_children(_goal, _handle):
            seen.append(self.container.get_active_children(GOAL_ID))
            return result_with(TaskStatus.DONE)

        self.container.execute_task_cb.side_effect = record_children

        self.container.execute_cb(make_goal_handle([subtask("a"), subtask("b")]))

        self.assertEqual(seen, [["a"], ["b"]])
        self.assertEqual(self.container.get_active_children(GOAL_ID), [])

    def test_get_task_specs(self):
        """Container is a non-blocking, reentrant action task, cancelled on stop, with the given cancel timeout."""
        specs = Container.get_task_specs("topic", cancel_timeout=7.0)
        self.assertEqual(specs.task_name, "system/container")
        self.assertEqual(specs.task_server_type, TaskServerType.ACTION)
        self.assertFalse(specs.blocking)
        self.assertTrue(specs.reentrant)
        self.assertTrue(specs.cancel_on_stop)
        self.assertEqual(specs.cancel_timeout, 7.0)


if __name__ == "__main__":
    unittest.main()
