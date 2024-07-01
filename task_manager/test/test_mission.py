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

            result = self.mission.execute_cb(goal_handle=Mock(request=request))
            self.assertEqual(result, expected_result)

        with self.subTest("Successful flow with randomly generated task ID"):
            request.subtasks[0].task_id = ""
            result = self.mission.execute_cb(goal_handle=Mock(request=request))
            self.assertEqual(result.mission_results[0].task_id, "123")

    def test_mission_not_successful(self):
        """Tests that the status of the subtasks are set correctly when the subtasks fail or are cancelled, or if the
        Mission is cancelled."""
        request = MissionAction.Goal(subtasks=[SubtaskGoal(task_name="test/mock_subtask", task_data="{}")])
        mock_goal_handle = Mock(request=request)

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
        mock_handle = Mock(request=request)
        mock_handle.is_cancel_requested = False
        result = self.mission.execute_cb(goal_handle=mock_handle)
        self.assertEqual(result, expected_result)


if __name__ == "__main__":
    unittest.main()
