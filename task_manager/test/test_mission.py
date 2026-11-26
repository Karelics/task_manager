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

import threading
import time
import unittest
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from unittest import mock
from unittest.mock import Mock, patch

# Task Manager messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.action import Mission as MissionAction
from task_manager_msgs.msg import SubtaskGoal, SubtaskResult, TaskStatus

# Task Manager
from task_manager.tasks.mission import Mission


class MissionUnittest(unittest.TestCase):
    """Unittests for Mission Task."""

    def setUp(self):
        self.patcher = mock.patch("task_manager.tasks.mission.ActionServer", return_value=Mock())
        self.patcher.start()

        self.mission = Mission(node=Mock(), action_name="", execute_task_cb=Mock())

    def tearDown(self):
        self.patcher.stop()

    @patch("task_manager.tasks.mission.uuid.uuid4", return_value="123")
    def test_execute_cb(self, _mock_generate_random_uuid):
        """Test successful flow of launching the mission."""
        request = MissionAction.Goal()
        request.subtasks = [SubtaskGoal(task_name="test/mock_subtask", task_data="{}")]
        self.mission.execute_task_cb.return_value = ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        with self.subTest("Successful flow"):
            request.subtasks[0].task_id = "111"
            expected_result = MissionAction.Result()
            expected_result.mission_results = [
                SubtaskResult(task_name="test/mock_subtask", task_status=TaskStatus.DONE, task_id="111")
            ]

            result = self.mission.execute_cb(
                goal_handle=Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)
            )
            self.assertEqual(result, expected_result)

        with self.subTest("Successful flow with randomly generated task ID"):
            request.subtasks[0].task_id = ""
            result = self.mission.execute_cb(
                goal_handle=Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)
            )
            self.assertEqual(result.mission_results[0].task_id, "123")

    def test_get_active_children_tracks_progress(self):
        """Mission tracks the task_id of the currently running subtask.

        Get active children should always return the task_id of the currently running subtask. Tracking is cleared once
        the mission finishes.
        """
        request = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task_name="subtask_a", task_data="{}", task_id="a"),
                SubtaskGoal(task_name="subtask_b", task_data="{}", task_id="b"),
            ]
        )
        goal_id = bytes([0] * 16)
        first = True

        def current_subtask_check():
            nonlocal first
            if first:
                first = False
                return ["a"]
            return ["b"]

        def fake_execute_task_cb(_goal, _goal_handle):
            self.assertEqual(current_subtask_check(), self.mission.get_active_children(goal_id))
            return ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        self.mission.execute_task_cb.side_effect = fake_execute_task_cb

        self.assertEqual(self.mission.get_active_children(goal_id), [])
        self.mission.execute_cb(
            goal_handle=Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)
        )

        self.assertEqual(self.mission.get_active_children(goal_id), [])

    def test_get_active_children_per_invocation(self):
        """Concurrently running missions must not clobber each other's tracked subtask.

        Two missions with distinct goal_ids each track their own active subtask independently.
        """
        goal_id_a = bytes([1] * 16)
        goal_id_b = bytes([2] * 16)
        request_a = MissionAction.Goal(subtasks=[SubtaskGoal(task_name="subtask_a", task_data="{}", task_id="a")])
        request_b = MissionAction.Goal(subtasks=[SubtaskGoal(task_name="subtask_b", task_data="{}", task_id="b")])

        def fake_execute_task_cb(goal, _goal_handle):
            if goal.task_id == "a":
                # Simulate mission B running concurrently while mission A is still mid-flight
                self.mission.execute_cb(
                    goal_handle=Mock(request=request_b, goal_id=Mock(uuid=list(goal_id_b)), is_cancel_requested=False)
                )
                self.assertEqual(self.mission.get_active_children(goal_id_a), ["a"])
            return ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        self.mission.execute_task_cb.side_effect = fake_execute_task_cb
        self.mission.execute_cb(
            goal_handle=Mock(request=request_a, goal_id=Mock(uuid=list(goal_id_a)), is_cancel_requested=False)
        )

        self.assertEqual(self.mission.get_active_children(goal_id_a), [])
        self.assertEqual(self.mission.get_active_children(goal_id_b), [])

    def test_pause_resume_unknown_goal_id_are_no_ops(self):
        """request_pause/request_resume/is_paused are no-ops for an unknown goal_id.

        They return False for a goal_id that isn't a currently running mission invocation.
        """
        unknown_goal_id = bytes([9] * 16)
        self.assertFalse(self.mission.request_pause(unknown_goal_id))
        self.assertFalse(self.mission.request_resume(unknown_goal_id))
        self.assertFalse(self.mission.is_paused(unknown_goal_id))

    @contextmanager
    def _run_mission(self, goal_handle) -> Generator[Future, None, None]:
        """Runs the mission's execute_cb in a worker thread and yields its future.

        Use future.result(timeout=...) to assert that it returns: it raises TimeoutError if it does not, and re-raises
        any error from the worker. On exit the worker is always stopped and joined.
        """
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.mission.execute_cb, goal_handle)
            try:
                yield future
            finally:
                goal_handle.is_cancel_requested = True  # Lets the worker exit if the test failed early

    @staticmethod
    def _wait_until(predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_pause_holds_next_subtask_until_resumed(self):
        """Pausing while the current subtask finishes must hold off starting the next one.

        Covers a service-backed subtask that can't actually be paused - execute_cb must still hold before
        starting the next subtask, until resumed.
        """
        request = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task_name="subtask_a", task_data="{}", task_id="a"),
                SubtaskGoal(task_name="subtask_b", task_data="{}", task_id="b"),
            ]
        )
        goal_id = bytes([0] * 16)
        dispatched = []

        def fake_execute_task_cb(goal, _goal_handle):
            dispatched.append(goal.task_id)
            if goal.task_id == "a":
                self.mission.request_pause(goal_id)
            return ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        self.mission.execute_task_cb.side_effect = fake_execute_task_cb
        goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)

        with self._run_mission(goal_handle) as future:
            self.assertTrue(self._wait_until(lambda: self.mission.is_paused(goal_id)))
            self.assertEqual(dispatched, ["a"])

            self.mission.request_resume(goal_id)
            future.result(timeout=2)
            self.assertEqual(dispatched, ["a", "b"])
            goal_handle.succeed.assert_called_once()

    def test_paused_mission_can_still_be_cancelled(self):
        """A cancel while the mission is paused, ends the mission."""
        request = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task_name="subtask_a", task_data="{}", task_id="a"),
                SubtaskGoal(task_name="subtask_b", task_data="{}", task_id="b"),
            ]
        )
        goal_id = bytes([0] * 16)
        dispatched = []

        def fake_execute_task_cb(goal, _goal_handle):
            dispatched.append(goal.task_id)
            if goal.task_id == "a":
                self.mission.request_pause(goal_id)
            return ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        self.mission.execute_task_cb.side_effect = fake_execute_task_cb
        goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)

        with self._run_mission(goal_handle) as future:
            self.assertTrue(self._wait_until(lambda: self.mission.is_paused(goal_id)))
            goal_handle.is_cancel_requested = True
            future.result(timeout=2)
            goal_handle.canceled.assert_called_once()
            self.assertEqual(dispatched, ["a"])

    def test_pause_holds_next_subtask_when_skipping(self):
        """Pausing still holds the next subtask when the current one errors and is skippable."""
        request = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task_name="subtask_a", task_data="{}", task_id="a", allow_skipping=True),
                SubtaskGoal(task_name="subtask_b", task_data="{}", task_id="b"),
            ]
        )
        goal_id = bytes([0] * 16)
        subtask_b_started = threading.Event()

        def fake_execute_task_cb(goal, _goal_handle):
            if goal.task_id == "a":
                self.mission.request_pause(goal_id)
                return ExecuteTask.Result(task_status=TaskStatus.ERROR, task_result="{}")
            subtask_b_started.set()
            return ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        self.mission.execute_task_cb.side_effect = fake_execute_task_cb
        goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)

        with self._run_mission(goal_handle) as future:
            self.assertTrue(self._wait_until(lambda: self.mission.is_paused(goal_id)))

            # "b" must not start while paused - wait for a moment to show that it doesn't.
            self.assertFalse(self._wait_until(subtask_b_started.is_set, timeout=0.2))

            self.mission.request_resume(goal_id)
            self.assertTrue(subtask_b_started.wait(timeout=2))
            future.result(timeout=2)
            goal_handle.succeed.assert_called_once()

    def test_last_subtask_outcome(self):
        """Tests mission end when the mission's last subtask finishes.

        All combinations of subtask status, allow_skipping, and mission cancel requested are tested and the expected
        outcome for the mission is verified, as well as the recorded status of the last subtask.
        """
        cases = [
            # (subtask status, allow_skipping, mission cancel requested, expected goal handle call, expected skipped)
            (TaskStatus.DONE, False, False, "succeed", False),
            (TaskStatus.DONE, True, False, "succeed", False),
            (TaskStatus.DONE, False, True, "succeed", False),
            (TaskStatus.DONE, True, True, "succeed", False),
            (TaskStatus.ERROR, False, False, "abort", False),
            (TaskStatus.ERROR, True, False, "succeed", True),  # see test_allow_skipping_all_fail
            # (TaskStatus.ERROR, True, False, "abort", True),  # see test_allow_skipping_all_fail
            (TaskStatus.ERROR, False, True, "abort", False),  # TODO: Set to canceled - cancel was requested
            (TaskStatus.ERROR, True, True, "abort", False),  # TODO: Set to canceled - cancel was requested
            (TaskStatus.CANCELED, False, False, "abort", False),  # Cannot set to canceled without request
            (TaskStatus.CANCELED, True, False, "succeed", True),  # Skipping leads to success
            (TaskStatus.CANCELED, False, True, "canceled", False),
            (TaskStatus.CANCELED, True, True, "canceled", False),
        ]
        for subtask_status, allow_skipping, cancel_requested, expected_call, expected_skipped in cases:
            with self.subTest(status=subtask_status, allow_skipping=allow_skipping, cancel_requested=cancel_requested):
                request = MissionAction.Goal(
                    subtasks=[SubtaskGoal(task_name="test/mock_subtask", allow_skipping=allow_skipping, task_data="{}")]
                )
                mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)

                def execute_task(
                    _goal, _goal_handle, mission_handle=mock_goal_handle, cancel=cancel_requested, status=subtask_status
                ):
                    mission_handle.is_cancel_requested = cancel
                    return ExecuteTask.Result(task_status=status, task_result="{}")

                self.mission.execute_task_cb.side_effect = execute_task

                result = self.mission.execute_cb(goal_handle=mock_goal_handle)

                for call in ("succeed", "abort", "canceled"):
                    mock = getattr(mock_goal_handle, call)
                    if call == expected_call:
                        mock.assert_called_once()
                    else:
                        mock.assert_not_called()
                self.assertEqual(result.mission_results[0].task_status, subtask_status)
                self.assertEqual(result.mission_results[0].skipped, expected_skipped)

    def test_failed_subtask_after_a_skipped_one_aborts(self):
        """A skipped subtask does not carry over to the next one: when that fails too, the mission aborts, is not marked
        skipped, and the remaining subtasks are not started."""
        request = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task_name="test/mock_subtask", task_data="{}", allow_skipping=True, task_id="1"),
                SubtaskGoal(task_name="test/mock_subtask", task_data="{}", task_id="2"),
                SubtaskGoal(task_name="test/mock_subtask", task_data="{}", task_id="3"),
            ]
        )
        # A third call would raise StopIteration, failing the test.
        self.mission.execute_task_cb.side_effect = [
            ExecuteTask.Result(task_status=TaskStatus.ERROR, task_result="{}"),
            ExecuteTask.Result(task_status=TaskStatus.ERROR, task_result="{}"),
        ]
        mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)

        result = self.mission.execute_cb(goal_handle=mock_goal_handle)

        mock_goal_handle.abort.assert_called_once()
        mock_goal_handle.succeed.assert_not_called()
        mock_goal_handle.canceled.assert_not_called()
        self.assertEqual(
            [(r.task_status, r.skipped) for r in result.mission_results],
            [(TaskStatus.ERROR, True), (TaskStatus.ERROR, False), (TaskStatus.RECEIVED, False)],
        )

    @unittest.expectedFailure
    def test_allow_skipping_all_fail(self):
        """If all subtasks are allowed to be skipped and fail, the mission should abort.

        In this case it sounds silly if the mission were to succeed despite all subtasks failing.

        TODO: Implement proper handling for missions where all subtasks are allowed to be skipped and fail.
        """
        request = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task_name="test/mock_subtask", allow_skipping=True, task_data="{}", task_id="123"),
                SubtaskGoal(task_name="test/mock_subtask", allow_skipping=True, task_data="{}", task_id="456"),
            ]
        )
        mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)

        def execute_task(_goal, handle):
            return ExecuteTask.Result(task_status=TaskStatus.ERROR, task_result="{}")

        self.mission.execute_task_cb.side_effect = execute_task

        self.mission.execute_cb(goal_handle=mock_goal_handle)

        mock_goal_handle.abort.assert_called_once()
        mock_goal_handle.succeed.assert_not_called()
        mock_goal_handle.canceled.assert_not_called()

    def test_subtasks_are_started_with_mission_as_source(self):
        """TaskRegistrator relies on the "Mission" source to not cancel a nested mission's own outer mission."""
        request = MissionAction.Goal(
            subtasks=[SubtaskGoal(task_name="test/mock_subtask", task_data='{"a": 1}', task_id="123")]
        )
        self.mission.execute_task_cb.return_value = ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")
        mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)

        self.mission.execute_cb(goal_handle=mock_goal_handle)

        goal = self.mission.execute_task_cb.call_args.args[0]
        self.assertEqual(
            goal, ExecuteTask.Goal(task_id="123", task_name="test/mock_subtask", task_data='{"a": 1}', source="Mission")
        )

    def test_cancel_between_subtasks_does_not_start_the_next_subtask(self):
        """A cancel that arrives after a subtask finished cancels the mission before the next subtask is dispatched."""
        request = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task_name="test/mock_subtask", task_data="{}", task_id="123"),
                SubtaskGoal(task_name="test/mock_subtask", task_data="{}", task_id="456"),
            ]
        )
        mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)

        def execute_task(_goal, handle):
            handle.is_cancel_requested = True
            return ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        self.mission.execute_task_cb.side_effect = execute_task

        result = self.mission.execute_cb(goal_handle=mock_goal_handle)

        self.mission.execute_task_cb.assert_called_once()
        mock_goal_handle.canceled.assert_called_once()
        mock_goal_handle.succeed.assert_not_called()
        mock_goal_handle.abort.assert_not_called()
        self.assertEqual(
            [subtask_result.task_status for subtask_result in result.mission_results],
            [TaskStatus.DONE, TaskStatus.RECEIVED],
        )

    def test_skipping_subtask(self):
        """Tests that even though a subtask fails, the mission does not abort when the subtask is allowed to be skipped.

        The mission continues with the next subtask and succeeds.
        """
        request = MissionAction.Goal()
        request.subtasks = [
            SubtaskGoal(task_name="test/mock_subtask", task_data="{}", allow_skipping=True, task_id="123"),
            SubtaskGoal(task_name="test/mock_subtask", task_data="{}", task_id="456"),
        ]

        self.mission.execute_task_cb.side_effect = [
            ExecuteTask.Result(task_status=TaskStatus.ERROR, task_result="{}"),
            ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}"),
        ]

        expected_result = MissionAction.Result()
        expected_result.mission_results = [
            SubtaskResult(task_name="test/mock_subtask", task_status=TaskStatus.ERROR, skipped=True, task_id="123"),
            SubtaskResult(task_name="test/mock_subtask", task_status=TaskStatus.DONE, skipped=False, task_id="456"),
        ]
        mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16), is_cancel_requested=False)
        result = self.mission.execute_cb(goal_handle=mock_goal_handle)

        self.assertEqual(result, expected_result)
        self.assertEqual(self.mission.execute_task_cb.call_count, 2)
        mock_goal_handle.succeed.assert_called_once()
        mock_goal_handle.abort.assert_not_called()


if __name__ == "__main__":
    unittest.main()
