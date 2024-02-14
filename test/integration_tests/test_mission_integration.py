#!/usr/bin/env python3

#  ------------------------------------------------------------------
#   Copyright (C) Karelics Oy - All Rights Reserved
#   Unauthorized copying of this file, via any medium is strictly
#   prohibited. All information contained herein is, and remains
#   the property of Karelics Oy.
#  ------------------------------------------------------------------

import unittest

# ROS
from rclpy.action import ActionClient

# Thirdparty
from task_manager_test_utils import TaskManagerTestNode

# ROS messages
from action_msgs.msg import GoalStatus

# Karelics messages
from task_manager_msgs.action import Mission as MissionAction
from task_manager_msgs.msg import SubtaskGoal, TaskStatus


class MissionTests(TaskManagerTestNode):
    """Integration tests for verifying the functionality of the Mission."""

    def test_start_mission_task_success(self):
        """Integration test for successful flow of the mission task."""
        goal = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task="fibonacci", data='{"order": 0}'),
                SubtaskGoal(task="add_two_ints", data='{"a": 0, "b": 0}'),
            ]
        )
        mission_client = ActionClient(self.task_manager_node, MissionAction, "/test/task/system/mission")
        mission_client.wait_for_server(5)
        result = mission_client.send_goal(goal)

        self.assertEqual(result.status, GoalStatus.STATUS_SUCCEEDED)
        self.assertEqual(result.result.mission_results[0].status, TaskStatus.DONE)
        self.assertEqual(result.result.mission_results[1].status, TaskStatus.DONE)

    def test_start_mission_task_cancelled_on_new_blocking_task(self):
        """Checks that a new blocking task cancels the whole mission."""
        goal = MissionAction.Goal(
            subtasks=[
                SubtaskGoal(task="fibonacci_blocking", data='{"order": 5}', task_id="123"),
                SubtaskGoal(task="fibonacci_blocking_2", data='{"order": 1}'),
            ]
        )

        mission_client = ActionClient(self.task_manager_node, MissionAction, "/test/task/system/mission")
        mission_client.wait_for_server(5)
        future = mission_client.send_goal_async(goal)
        mission_goal_handle = self._get_response(future, timeout=5)

        # In the future, we need to have a way to abort the whole mission when we get a new blocking task, instead of
        # simply cancelling the individual sub-tasks. Now we might get a situation that while mission is running a
        # non-blocking task, we can start another blocking task, allowing the mission to still continue.
        self.wait_for_task_start("123")

        fib_goal_handle = self.start_fibonacci_action_task("fibonacci_blocking", run_time_secs=0)

        mission_result = mission_goal_handle.get_result()
        fibonacci_result = fib_goal_handle.get_result()

        self.assertEqual(mission_result.status, GoalStatus.STATUS_ABORTED)
        self.assertEqual(mission_result.result.mission_results[0].status, TaskStatus.CANCELED)
        self.assertEqual(mission_result.result.mission_results[1].status, TaskStatus.RECEIVED)
        self.assertEqual(fibonacci_result.status, GoalStatus.STATUS_SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
