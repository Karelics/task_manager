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
from rosbridge_library.internal.message_conversion import extract_values, populate_instance
from task_manager_test_utils import TaskManagerTestNode

# ROS messages
from action_msgs.msg import GoalStatus

# Task Manager messages
from task_manager_msgs.action import ExecuteTask, Mission
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

    def test_pause_mission(self):
        """The currently running subtask should be paused and mission status should change to PAUSED."""
        mission_goal = Mission.Goal(
            subtasks=[
                SubtaskGoal(task_name="fibonacci", task_data='{"order": 5}', task_id="123"),
                SubtaskGoal(task_name="add_two_ints", task_data='{"a": 0, "b": 0}', task_id="456"),
            ]
        )

        goal = ExecuteTask.Goal()
        goal.task_name = "system/mission"
        goal.task_data = json.dumps(extract_values(mission_goal))

        future = self.execute_task_client.send_goal_async(goal)
        self._get_response(future, timeout=5)

        self.wait_for_task_start("123")
        active_tasks_by_id = {
            task.task_details.task_id: task for task in self.task_manager_node.active_tasks.get_active_tasks()
        }
        mission_id = next(
            task_id for task_id, task in active_tasks_by_id.items() if task.task_specs.task_name == "system/mission"
        )

        self.execute_pause_task([mission_id])

        self.assertEqual(active_tasks_by_id[mission_id].task_details.status, TaskStatus.PAUSED)
        self.assertEqual(active_tasks_by_id["123"].task_details.status, TaskStatus.PAUSED)
        # The not-yet-started subtask must stay untouched - it never even reached ActiveTasks
        self.assertNotIn("456", self._tasks_started)

    def test_resume_mission(self):
        """Resuming a paused mission replays its currently running subtask and the mission status goes back to
        IN_PROGRESS.

        The mission then continues normally to completion.
        """
        mission_goal = Mission.Goal(
            subtasks=[
                SubtaskGoal(task_name="fibonacci_blocking", task_data='{"order": 3}', task_id="123"),
                SubtaskGoal(task_name="add_two_ints", task_data='{"a": 0, "b": 0}', task_id="456"),
            ]
        )

        goal = ExecuteTask.Goal()
        goal.task_name = "system/mission"
        goal.task_data = json.dumps(extract_values(mission_goal))

        future = self.execute_task_client.send_goal_async(goal)
        mission_goal_handle = self._get_response(future, timeout=5)

        self.wait_for_task_start("123")
        active_tasks_by_id = {
            task.task_details.task_id: task for task in self.task_manager_node.active_tasks.get_active_tasks()
        }
        mission_id = next(
            task_id for task_id, task in active_tasks_by_id.items() if task.task_specs.task_name == "system/mission"
        )

        self.execute_pause_task([mission_id])
        self.wait_for_task_status("123", TaskStatus.PAUSED)
        self.assertEqual(active_tasks_by_id[mission_id].task_details.status, TaskStatus.PAUSED)
        # The not-yet-started subtask must stay untouched - it never even reached ActiveTasks
        self.assertNotIn("456", self._tasks_started)

        # Resuming any task related to the mission should resume the whole mission
        resume_response = self.execute_resume_task(["123"])
        self.assertEqual(resume_response.result.task_status, TaskStatus.DONE)
        self.assertEqual(
            resume_response.result.task_result, json.dumps({"success": True, "successful_resumes": ["123"]})
        )

        self.wait_for_task_status("123", TaskStatus.IN_PROGRESS)
        self.assertEqual(active_tasks_by_id[mission_id].task_details.status, TaskStatus.IN_PROGRESS)

        mission_response = mission_goal_handle.get_result()
        mission_result = populate_instance(json.loads(mission_response.result.task_result), Mission.Result())

        self.assertEqual(mission_response.status, GoalStatus.STATUS_SUCCEEDED)
        self.assertEqual(mission_result.mission_results[0].task_status, TaskStatus.DONE)
        self.assertEqual(mission_result.mission_results[1].task_status, TaskStatus.DONE)


if __name__ == "__main__":
    unittest.main()
