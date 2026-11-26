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
from task_manager_msgs.action import AtomicSequence as AtomicSequenceAction
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import SubtaskGoal, TaskStatus

# Task Manager
from task_manager.task_specs import TaskServerType
from task_manager.tasks.atomic_sequence import AtomicSequence

GOAL_ID = bytes([0] * 16)


def make_goal_handle(subtasks, is_cancel_requested=False):
    """Mock ServerGoalHandle for an atomic sequence goal with the given subtasks."""
    return Mock(
        request=AtomicSequenceAction.Goal(subtasks=subtasks),
        goal_id=Mock(uuid=[0] * 16),
        is_cancel_requested=is_cancel_requested,
    )


def subtask(task_id, allow_skipping=False):
    """SubtaskGoal with the given task_id."""
    return SubtaskGoal(task_name=f"task_{task_id}", task_data="{}", task_id=task_id, allow_skipping=allow_skipping)


def result_with(status):
    """ExecuteTask.Result with the given status."""
    return ExecuteTask.Result(task_status=status, task_result="{}")


class AtomicSequenceUnittest(unittest.TestCase):
    """Unittests for AtomicSequence Task."""

    def setUp(self):
        self.patcher = mock.patch("task_manager.tasks.atomic_sequence.ActionServer", return_value=Mock())
        self.patcher.start()

        self.atomic_sequence = AtomicSequence(node=Mock(), action_name="", execute_task_cb=Mock())

    def tearDown(self):
        self.patcher.stop()

    def test_all_subtasks_succeed(self):
        """Subtasks run in order with source AtomicSequence and the atomic sequence succeeds."""
        self.atomic_sequence.execute_task_cb.return_value = result_with(TaskStatus.DONE)
        goal_handle = make_goal_handle([subtask("a"), subtask("b")])

        result = self.atomic_sequence.execute_cb(goal_handle)

        started = [call.args[0] for call in self.atomic_sequence.execute_task_cb.call_args_list]
        self.assertEqual([goal.task_id for goal in started], ["a", "b"])
        self.assertTrue(all(goal.source == "AtomicSequence" for goal in started))
        self.assertEqual([r.task_status for r in result.results], [TaskStatus.DONE, TaskStatus.DONE])
        goal_handle.succeed.assert_called_once()

    @patch("task_manager.tasks.mission.uuid.uuid4", return_value="123")
    def test_generates_missing_task_ids(self, _mock_uuid):
        """Subtasks without a task_id get a generated one."""
        self.atomic_sequence.execute_task_cb.return_value = result_with(TaskStatus.DONE)
        result = self.atomic_sequence.execute_cb(make_goal_handle([subtask("")]))
        self.assertEqual(result.results[0].task_id, "123")

    def test_failed_subtask_aborts(self):
        """A failed subtask aborts the atomic sequence and the remaining subtasks are not started."""
        self.atomic_sequence.execute_task_cb.return_value = result_with(TaskStatus.ERROR)
        goal_handle = make_goal_handle([subtask("a"), subtask("b")])

        result = self.atomic_sequence.execute_cb(goal_handle)

        self.assertEqual(self.atomic_sequence.execute_task_cb.call_count, 1)
        self.assertEqual([r.task_status for r in result.results], [TaskStatus.ERROR, TaskStatus.RECEIVED])
        goal_handle.abort.assert_called_once()
        goal_handle.succeed.assert_not_called()

    def test_failed_skippable_subtask_continues(self):
        """A failed subtask with allow_skipping is marked skipped and the atomic sequence continues."""
        self.atomic_sequence.execute_task_cb.side_effect = [result_with(TaskStatus.ERROR), result_with(TaskStatus.DONE)]
        goal_handle = make_goal_handle([subtask("a", allow_skipping=True), subtask("b")])

        result = self.atomic_sequence.execute_cb(goal_handle)

        self.assertTrue(result.results[0].skipped)
        self.assertFalse(result.results[1].skipped)
        goal_handle.succeed.assert_called_once()

    def test_cancel_before_first_subtask(self):
        """A cancel (or pause) arriving before the first subtask starts doesn't start anything."""
        goal_handle = make_goal_handle([subtask("a")], is_cancel_requested=True)

        self.atomic_sequence.execute_cb(goal_handle)

        self.atomic_sequence.execute_task_cb.assert_not_called()
        goal_handle.canceled.assert_called_once()

    def test_cancel_between_subtasks(self):
        """A cancel (or pause) arriving between two subtasks doesn't start the next one."""
        goal_handle = make_goal_handle([subtask("a"), subtask("b")])

        def finish_then_cancel(_goal, handle):
            handle.is_cancel_requested = True
            return result_with(TaskStatus.DONE)

        self.atomic_sequence.execute_task_cb.side_effect = finish_then_cancel

        self.atomic_sequence.execute_cb(goal_handle)

        self.assertEqual(self.atomic_sequence.execute_task_cb.call_count, 1)
        goal_handle.canceled.assert_called_once()
        goal_handle.succeed.assert_not_called()

    def test_cancel_ends_canceled_even_if_subtask_aborts(self):
        """If the running subtask reports an error instead of CANCELED when the atomic sequence is cancelled (e.g. its
        server aborts on cancel), the atomic sequence must still end CANCELED, so that a pause is recognised as a
        pause."""
        goal_handle = make_goal_handle([subtask("a"), subtask("b")])

        def abort_on_cancel(_goal, handle):
            handle.is_cancel_requested = True
            return result_with(TaskStatus.ERROR)

        self.atomic_sequence.execute_task_cb.side_effect = abort_on_cancel

        self.atomic_sequence.execute_cb(goal_handle)

        goal_handle.canceled.assert_called_once()
        goal_handle.abort.assert_not_called()

    def test_last_subtask_outcome(self):
        """Tests how the atomic sequence ends, and what is recorded for its (only, so last) subtask, for every
        combination of the subtask's status, `allow_skipping` and a cancel (or pause) of the atomic sequence arriving
        while the subtask runs.

        A cancel arriving after the last subtask already finished DONE is too late, so the atomic sequence succeeds.
        Otherwise a cancel ends it CANCELED, whatever the subtask reported (its server may abort on cancel) and even if
        the subtask is skippable, so that a pause is not mistaken for a failure or a skip. Only a subtask that could not
        be cancelled (still IN_PROGRESS) aborts the atomic sequence, so that a failed pause is not reported as success.
        """
        cases = [
            # (subtask status, allow_skipping, cancel requested, expected goal handle call, expected skipped)
            (TaskStatus.DONE, False, False, "succeed", False),
            (TaskStatus.DONE, True, False, "succeed", False),
            (TaskStatus.DONE, False, True, "succeed", False),
            (TaskStatus.DONE, True, True, "succeed", False),
            (TaskStatus.ERROR, False, False, "abort", False),
            # The last subtask is skipped, but the atomic sequence still reports success. Pins the current behaviour.
            (TaskStatus.ERROR, True, False, "succeed", True),
            (TaskStatus.ERROR, False, True, "canceled", False),
            (TaskStatus.ERROR, True, True, "canceled", False),
            (TaskStatus.CANCELED, False, False, "abort", False),
            # Same as the skipped ERROR above. Pins the current behaviour.
            (TaskStatus.CANCELED, True, False, "succeed", True),
            (TaskStatus.CANCELED, False, True, "canceled", False),
            (TaskStatus.CANCELED, True, True, "canceled", False),
            (TaskStatus.IN_PROGRESS, False, True, "abort", False),
            (TaskStatus.IN_PROGRESS, True, True, "abort", False),
        ]
        for subtask_status, allow_skipping, cancel_requested, expected_call, expected_skipped in cases:
            with self.subTest(status=subtask_status, allow_skipping=allow_skipping, cancel_requested=cancel_requested):

                def execute_task(_goal, goal_handle):
                    goal_handle.is_cancel_requested = cancel_requested
                    return result_with(subtask_status)

                self.atomic_sequence.execute_task_cb.side_effect = execute_task
                goal_handle = make_goal_handle([subtask("a", allow_skipping=allow_skipping)])

                result = self.atomic_sequence.execute_cb(goal_handle)

                for call in ("succeed", "abort", "canceled"):
                    mock = getattr(goal_handle, call)
                    if call == expected_call:
                        mock.assert_called_once()
                    else:
                        mock.assert_not_called()
                self.assertEqual(result.results[0].task_status, subtask_status)
                self.assertEqual(result.results[0].skipped, expected_skipped)

    def test_get_active_children_tracks_progress(self):
        """The currently running subtask is reported as the only active child, cleared once the atomic sequence
        finishes."""
        seen = []

        def record_children(_goal, _handle):
            seen.append(self.atomic_sequence.get_active_children(GOAL_ID))
            return result_with(TaskStatus.DONE)

        self.atomic_sequence.execute_task_cb.side_effect = record_children

        self.atomic_sequence.execute_cb(make_goal_handle([subtask("a"), subtask("b")]))

        self.assertEqual(seen, [["a"], ["b"]])
        self.assertEqual(self.atomic_sequence.get_active_children(GOAL_ID), [])

    def test_get_task_specs(self):
        """AtomicSequence is a non-blocking, reentrant action task, cancelled on stop, with the given cancel timeout."""
        specs = AtomicSequence.get_task_specs("topic", cancel_timeout=7.0)
        self.assertEqual(specs.task_name, "system/atomic_sequence")
        self.assertEqual(specs.task_server_type, TaskServerType.ACTION)
        self.assertFalse(specs.blocking)
        self.assertTrue(specs.reentrant)
        self.assertTrue(specs.cancel_on_stop)
        self.assertEqual(specs.cancel_timeout, 7.0)


if __name__ == "__main__":
    unittest.main()
