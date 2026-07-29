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

            result = self.mission.execute_cb(goal_handle=Mock(request=request, goal_id=Mock(uuid=[0] * 16)))
            self.assertEqual(result, expected_result)

        with self.subTest("Successful flow with randomly generated task ID"):
            request.subtasks[0].task_id = ""
            result = self.mission.execute_cb(goal_handle=Mock(request=request, goal_id=Mock(uuid=[0] * 16)))
            self.assertEqual(result.mission_results[0].task_id, "123")

    def test_get_active_children_tracks_progress_and_clears_after(self):
        """The mission tracks the task_id of whichever subtask is currently running, so that pausing/resuming the
        mission can be redirected to it.

        The tracking is cleared once the mission finishes.
        """
        request = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task_name="subtask_a", task_data="{}", task_id="a"),
                SubtaskGoal(task_name="subtask_b", task_data="{}", task_id="b"),
            ]
        )
        goal_id = bytes([0] * 16)
        seen_subtask_ids = []

        def fake_execute_task_cb(_goal, _goal_handle):
            seen_subtask_ids.append(self.mission.get_active_children(goal_id))
            return ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        self.mission.execute_task_cb.side_effect = fake_execute_task_cb

        self.assertEqual(self.mission.get_active_children(goal_id), [])
        self.mission.execute_cb(goal_handle=Mock(request=request, goal_id=Mock(uuid=[0] * 16)))

        self.assertEqual(seen_subtask_ids, [["a"], ["b"]])
        self.assertEqual(self.mission.get_active_children(goal_id), [])

    def test_get_active_children_is_independent_per_mission_invocation(self):
        """Two concurrently running missions (distinct goal_ids) must not clobber each other's tracked subtask."""
        goal_id_a = bytes([1] * 16)
        goal_id_b = bytes([2] * 16)
        request_a = MissionAction.Goal(subtasks=[SubtaskGoal(task_name="subtask_a", task_data="{}", task_id="a")])
        request_b = MissionAction.Goal(subtasks=[SubtaskGoal(task_name="subtask_b", task_data="{}", task_id="b")])

        def fake_execute_task_cb(goal, _goal_handle):
            if goal.task_id == "a":
                # Simulate mission B running concurrently while mission A is still mid-flight
                self.mission.execute_cb(goal_handle=Mock(request=request_b, goal_id=Mock(uuid=list(goal_id_b))))
                self.assertEqual(self.mission.get_active_children(goal_id_a), ["a"])
            return ExecuteTask.Result(task_status=TaskStatus.DONE, task_result="{}")

        self.mission.execute_task_cb.side_effect = fake_execute_task_cb
        self.mission.execute_cb(goal_handle=Mock(request=request_a, goal_id=Mock(uuid=list(goal_id_a))))

        self.assertEqual(self.mission.get_active_children(goal_id_a), [])
        self.assertEqual(self.mission.get_active_children(goal_id_b), [])

    def test_mission_not_successful(self):
        """Tests that the status of the subtasks are set correctly when the subtasks fail or are cancelled, or if the
        Mission is cancelled."""
        request = MissionAction.Goal(subtasks=[SubtaskGoal(task_name="test/mock_subtask", task_data="{}")])
        mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16))

        with self.subTest("canceled"):
            self.mission.execute_task_cb.return_value = ExecuteTask.Result(
                task_status=TaskStatus.CANCELED, task_result="{}"
            )
            result = self.mission.execute_cb(goal_handle=mock_goal_handle)
            mock_goal_handle.canceled.assert_called_once()
            self.assertEqual(result.mission_results[0].task_status, TaskStatus.CANCELED)

        mock_goal_handle.reset_mock()
        with self.subTest("error"):
            self.mission.execute_task_cb.return_value = ExecuteTask.Result(
                task_status=TaskStatus.ERROR, task_result="{}"
            )
            result = self.mission.execute_cb(goal_handle=mock_goal_handle)
            mock_goal_handle.abort.assert_called_once()
            self.assertEqual(result.mission_results[0].task_status, TaskStatus.ERROR)

    def test_mission_not_successful_skipping_task(self):
        """Tests that the mission is properly cancelled or aborted when a subtask is skipped."""
        request = MissionAction.Goal(
            subtasks=[SubtaskGoal(task_name="test/mock_subtask", allow_skipping=True, task_data="{}")]
        )
        mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16))
        mock_goal_handle.is_cancel_requested = True

        with self.subTest("mission_canceled"):
            self.mission.execute_task_cb.return_value = ExecuteTask.Result(
                task_status=TaskStatus.CANCELED, task_result="{}"
            )
            result = self.mission.execute_cb(goal_handle=mock_goal_handle)
            mock_goal_handle.canceled.assert_called_once()
            self.assertEqual(result.mission_results[0].task_status, TaskStatus.CANCELED)

        mock_goal_handle.reset_mock()
        with self.subTest("mission_error"):
            self.mission.execute_task_cb.return_value = ExecuteTask.Result(
                task_status=TaskStatus.ERROR, task_result="{}"
            )
            result = self.mission.execute_cb(goal_handle=mock_goal_handle)
            mock_goal_handle.abort.assert_called_once()
            self.assertEqual(result.mission_results[0].task_status, TaskStatus.ERROR)

    def test_skipping_subtask(self):
        """Tests that even though a subtask is aborted, no error is raised when the task is allowed to be skipped."""
        request = MissionAction.Goal()
        request.subtasks = [
            SubtaskGoal(task_name="test/mock_subtask", task_data="{}", allow_skipping=True, task_id="123")
        ]

        self.mission.execute_task_cb.return_value = ExecuteTask.Result(task_status=TaskStatus.ERROR, task_result="{}")

        expected_result = MissionAction.Result()
        expected_result.mission_results = [
            SubtaskResult(task_name="test/mock_subtask", task_status=TaskStatus.ERROR, skipped=True, task_id="123")
        ]
        mock_goal_handle = Mock(request=request, goal_id=Mock(uuid=[0] * 16))
        mock_goal_handle.is_cancel_requested = False
        result = self.mission.execute_cb(goal_handle=mock_goal_handle)
        self.assertEqual(result, expected_result)


if __name__ == "__main__":
    unittest.main()
