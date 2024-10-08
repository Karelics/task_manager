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

# ROS messages
from action_msgs.msg import GoalStatus

# Task Manager messages
from task_manager_msgs.msg import TaskStatus


class SystemTaskTests(TaskManagerTestNode):
    """Integration tests for verifying the functionality of system tasks."""

    def test_cancel_task_happy_flow(self) -> None:
        """Test task for canceling a specific task."""
        goal_handle = self.start_fibonacci_action_task(run_time_secs=5, task_id="111")
        self.wait_for_task_start("111")

        cancel_response = self.execute_cancel_task(task_ids=["111"])
        goal_handle.get_result()

        self.assertEqual(cancel_response.status, GoalStatus.STATUS_SUCCEEDED)
        self.assertEqual(cancel_response.result.task_status, TaskStatus.DONE)

        self.assertEqual(
            cancel_response.result.task_result, json.dumps({"success": True, "successful_cancels": ["111"]})
        )

        # Since the goal was cancelled from an external source, our execute_task client will have
        # status ABORTED, even though the Task will be CANCELED.
        self.assertEqual(goal_handle.get_result().status, GoalStatus.STATUS_ABORTED)
        self.assertEqual(goal_handle.get_result().result.task_status, TaskStatus.CANCELED)

    def test_cancel_task_non_existing_id(self) -> None:
        """Test trying to cancel a non-existing task."""
        cancel_response = self.execute_cancel_task(task_ids=["111"])

        self.assertEqual(cancel_response.result.task_status, TaskStatus.DONE)
        self.assertEqual(
            cancel_response.result.task_result, json.dumps({"success": True, "successful_cancels": ["111"]})
        )

    def test_cancel_non_cancelable_task(self) -> None:
        """Test trying to cancel a task that cannot be canceled."""
        self.task_manager_node.task_registrator.cancel_task_timeout = 0.1
        goal_handle = self.start_fibonacci_action_task(
            task_name="fibonacci_non_cancelable", run_time_secs=1, task_id="111"
        )
        self.wait_for_task_start("111")
        cancel_response = self.execute_cancel_task(task_ids=["111"])
        goal_response = goal_handle.get_result()

        self.assertEqual(cancel_response.result.task_status, TaskStatus.ERROR)
        self.assertEqual(cancel_response.result.task_result, json.dumps({"success": False, "successful_cancels": []}))
        self.assertEqual(goal_response.result.task_status, TaskStatus.DONE)

    def test_stop_task(self) -> None:
        """Test cases for Stop system task."""
        with self.subTest("Task with 'cancel_on_stop' field is cancelled"):
            goal_handle = self.start_fibonacci_action_task("fibonacci_cancel_on_stop", run_time_secs=10, task_id="111")
            self.wait_for_task_start("111")

            stop_response = self.execute_stop_task()
            self.assertEqual(stop_response.result.task_result, json.dumps({"success": True}))
            self.assertEqual(goal_handle.get_result().result.task_status, TaskStatus.CANCELED)

        with self.subTest("Normal task is not cancelled on STOP command"):
            goal_handle = self.start_fibonacci_action_task("fibonacci", run_time_secs=1, task_id="222")
            self.wait_for_task_start("222")

            stop_response = self.execute_stop_task()
            self.assertEqual(stop_response.result.task_result, json.dumps({"success": True}))
            self.assertEqual(goal_handle.get_result().result.task_status, TaskStatus.DONE)

        self.task_manager_node.task_registrator.cancel_task_timeout = 0.1
        with self.subTest("Task cancel fails"):
            goal_handle = self.start_fibonacci_action_task("fibonacci_non_cancelable", run_time_secs=1, task_id="111")
            self.wait_for_task_start("111")

            stop_response = self.execute_stop_task()
            self.assertEqual(stop_response.result.task_status, TaskStatus.ERROR)
            self.assertEqual(stop_response.result.task_result, json.dumps({"success": False}))
            self.assertEqual(goal_handle.get_result().status, GoalStatus.STATUS_SUCCEEDED)

    def test_wait_task(self) -> None:
        """Test cases for Wait system task."""
        with self.subTest("Wait task is successful"):
            wait_result = self.execute_wait_task(duration=0.5)
            self.assertEqual(wait_result.result.task_status, TaskStatus.DONE)
            self.assertEqual(wait_result.result.task_result, json.dumps({}))

        with self.subTest("Wait task is cancelled"):
            goal_handle = self.start_wait_task(duration=10.0, task_id="wait_cancel")
            self.wait_for_task_start("wait_cancel")

            cancel_response = self.execute_cancel_task(task_ids=["wait_cancel"])
            wait_result = goal_handle.get_result()

            self.assertEqual(cancel_response.status, GoalStatus.STATUS_SUCCEEDED)
            self.assertEqual(wait_result.status, GoalStatus.STATUS_ABORTED)
            self.assertEqual(wait_result.result.task_status, TaskStatus.CANCELED)

        with self.subTest("Input duration is negative - Wait indefinitely"):
            goal_handle = self.start_wait_task(duration=-1.0, task_id="wait_negative")
            self.wait_for_task_start("wait_negative")

            # Cancel the task to not wait for an eternity
            cancel_response = self.execute_cancel_task(task_ids=["wait_negative"])
            wait_result = goal_handle.get_result()

            self.assertEqual(cancel_response.status, GoalStatus.STATUS_SUCCEEDED)
            self.assertEqual(wait_result.result.task_status, TaskStatus.DONE)
            self.assertEqual(wait_result.result.task_result, json.dumps({}))

        with self.subTest("Input duration is zero - Wait indefinitely"):
            goal_handle = self.start_wait_task(duration=0.0, task_id="wait_indef_cancel")
            self.wait_for_task_start("wait_indef_cancel")

            # Cancel the task to not wait for an eternity
            cancel_response = self.execute_cancel_task(task_ids=["wait_indef_cancel"])
            wait_result = goal_handle.get_result()

            self.assertEqual(cancel_response.status, GoalStatus.STATUS_SUCCEEDED)
            self.assertEqual(wait_result.result.task_status, TaskStatus.DONE)
            self.assertEqual(wait_result.result.task_result, json.dumps({}))


if __name__ == "__main__":
    unittest.main()
