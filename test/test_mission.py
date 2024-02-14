#!/usr/bin/env python3

#  ------------------------------------------------------------------
#   Copyright (C) Karelics Oy - All Rights Reserved
#   Unauthorized copying of this file, via any medium is strictly
#   prohibited. All information contained herein is, and remains
#   the property of Karelics Oy.
#  ------------------------------------------------------------------

import unittest
from unittest import mock
from unittest.mock import Mock, patch

# Karelics messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.action import Mission as MissionAction
from task_manager_msgs.msg import SubtaskGoal, SubtaskResult, TaskStatus

# Current package
from task_manager.tasks.mission import Mission


class MissionUnittest(unittest.TestCase):
    """Unittests for Mission Task."""

    def setUp(self):
        self.patcher = mock.patch("task_manager.tasks.mission.ActionServer", return_value=Mock())
        self.patcher.start()

        self.mission = Mission(node=Mock(), task_topic_prefix="", execute_task_cb=Mock())

    def tearDown(self):
        self.patcher.stop()

    @patch("task_manager.tasks.mission.uuid.uuid4", return_value="123")
    def test_execute_cb(self, _mock_generate_random_uuid):
        """Test successful flow of launching the mission."""
        request = MissionAction.Goal()
        request.subtasks = [SubtaskGoal(task="test/mock_subtask", data="{}")]
        self.mission.execute_task_cb.return_value = ExecuteTask.Result(status=TaskStatus.DONE, result="{}")

        with self.subTest("Successful flow"):
            request.subtasks[0].task_id = "111"
            expected_result = MissionAction.Result()
            expected_result.mission_results = [
                SubtaskResult(task="test/mock_subtask", status=TaskStatus.DONE, task_id="111")
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
        request = MissionAction.Goal(subtasks=[SubtaskGoal(task="test/mock_subtask", data="{}")])
        mock_goal_handle = Mock(request=request)

        with self.subTest("canceled"):
            self.mission.execute_task_cb.return_value = ExecuteTask.Result(status=TaskStatus.CANCELED, result="{}")
            result = self.mission.execute_cb(goal_handle=mock_goal_handle)
            mock_goal_handle.canceled.assert_called_once()
            self.assertEqual(result.mission_results[0].status, TaskStatus.CANCELED)

        mock_goal_handle.reset_mock()
        with self.subTest("error"):
            self.mission.execute_task_cb.return_value = ExecuteTask.Result(status=TaskStatus.ERROR, result="{}")
            result = self.mission.execute_cb(goal_handle=mock_goal_handle)
            mock_goal_handle.abort.assert_called_once()
            self.assertEqual(result.mission_results[0].status, TaskStatus.ERROR)

    def test_skipping_subtask(self):
        """Tests that even tho a subtask is aborted, no error is raised when the task is allowed to be skipped."""
        request = MissionAction.Goal()
        request.subtasks = [SubtaskGoal(task="test/mock_subtask", data="{}", allow_skipping=True, task_id="123")]

        self.mission.execute_task_cb.return_value = ExecuteTask.Result(status=TaskStatus.ERROR, result="{}")

        expected_result = MissionAction.Result()
        expected_result.mission_results = [
            SubtaskResult(task="test/mock_subtask", status=TaskStatus.ERROR, skipped=True, task_id="123")
        ]

        result = self.mission.execute_cb(goal_handle=Mock(request=request))
        self.assertEqual(result, expected_result)


if __name__ == "__main__":
    unittest.main()
