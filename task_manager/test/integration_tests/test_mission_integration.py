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
import json
import unittest

# Thirdparty
from task_manager_test_utils import TaskManagerTestNode
from rosbridge_library.internal.message_conversion import extract_values, populate_instance

# ROS messages
from action_msgs.msg import GoalStatus

# Task Manager messages
from task_manager_msgs.action import Mission, ExecuteTask
from task_manager_msgs.msg import SubtaskGoal, TaskStatus


class MissionTests(TaskManagerTestNode):
    """Integration tests for verifying the functionality of the Mission."""

    def test_start_mission_task_success(self):
        """Integration test for successful flow of the mission task."""
        mission_goal = Mission.Goal(
            subtasks=[
                SubtaskGoal(task_name="fibonacci", task_data='{"order": 0}'),
                SubtaskGoal(task_name="add_two_ints", task_data='{"a": 0, "b": 0}'),
            ]
        )

        goal = ExecuteTask.Goal()
        goal.task_name = "system/mission"
        goal.task_data = json.dumps(extract_values(mission_goal))

        response = self.execute_task_client.send_goal(goal)
        self.assertEqual(response.status, GoalStatus.STATUS_SUCCEEDED)

        mission_result = populate_instance(json.loads(response.result.task_result), Mission.Result())
        self.assertEqual(mission_result.mission_results[0].task_status, TaskStatus.DONE)
        self.assertEqual(mission_result.mission_results[1].task_status, TaskStatus.DONE)

    def test_start_mission_task_cancelled_on_new_blocking_task(self):
        """Checks that a new blocking task cancels the whole mission."""
        mission_goal = Mission.Goal(
            subtasks=[
                SubtaskGoal(task_name="fibonacci_blocking", task_data='{"order": 5}', task_id="123"),
                SubtaskGoal(task_name="fibonacci_blocking_2", task_data='{"order": 1}'),
            ]
        )

        goal = ExecuteTask.Goal()
        goal.task_name = "system/mission"
        goal.task_data = json.dumps(extract_values(mission_goal))

        future = self.execute_task_client.send_goal_async(goal)
        mission_goal_handle = self._get_response(future, timeout=5)

        # In the future, we need to have a way to abort the whole mission when we get a new blocking task, instead of
        # simply cancelling the individual sub-tasks. Now we might get a situation that while mission is running a
        # non-blocking task, we can start another blocking task, allowing the mission to still continue.
        self.wait_for_task_start("123")

        fib_goal_handle = self.start_fibonacci_action_task("fibonacci_blocking", run_time_secs=0)

        mission_response = mission_goal_handle.get_result()
        fibonacci_result = fib_goal_handle.get_result()

        mission_result = populate_instance(json.loads(mission_response.result.task_result), Mission.Result())

        self.assertEqual(mission_response.status, GoalStatus.STATUS_ABORTED)
        self.assertEqual(mission_result.mission_results[0].task_status, TaskStatus.CANCELED)
        self.assertEqual(mission_result.mission_results[1].task_status, TaskStatus.RECEIVED)
        self.assertEqual(fibonacci_result.status, GoalStatus.STATUS_SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
