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

    def test_pause_and_resume_task_happy_flow(self) -> None:
        """Test pausing and then resuming a task."""
        goal_handle = self.start_fibonacci_action_task("fibonacci_blocking", run_time_secs=3, task_id="111")
        self.wait_for_task_start("111")

        pause_response = self.execute_pause_task(task_ids=["111"])
        self.assertEqual(pause_response.result.task_status, TaskStatus.DONE)
        self.assertEqual(pause_response.result.task_result, json.dumps({"success": True, "successful_pauses": ["111"]}))
        self.wait_for_task_status("111", TaskStatus.PAUSED)

        resume_response = self.execute_resume_task(task_ids=["111"])
        self.assertEqual(resume_response.result.task_status, TaskStatus.DONE)
        self.assertEqual(
            resume_response.result.task_result, json.dumps({"success": True, "successful_resumes": ["111"]})
        )
        self.wait_for_task_status("111", TaskStatus.IN_PROGRESS)

        # The task restarted from scratch after resuming and should still finish normally
        self.assertEqual(goal_handle.get_result().result.task_status, TaskStatus.DONE)

    def test_pause_task_non_existing_id(self) -> None:
        """Test trying to pause a non-existing task."""
        pause_response = self.execute_pause_task(task_ids=["111"])

        self.assertEqual(pause_response.result.task_status, TaskStatus.ERROR)
        self.assertEqual(pause_response.result.task_result, json.dumps({"success": False, "successful_pauses": []}))

    def test_pause_service_backed_task_fails(self) -> None:
        """Test trying to pause a task that is backed by a ROS service, which does not support real cancellation.

        The service call outlives the task's cancel_timeout (1.5s) grace period, so the pause is a real failure.
        """
        goal_handle = self.start_add_two_ints_service_task(task_id="222", run_time_secs=3)
        self.wait_for_task_start("222")

        pause_response = self.execute_pause_task(task_ids=["222"])
        self.assertEqual(pause_response.result.task_status, TaskStatus.ERROR)
        self.assertEqual(pause_response.result.task_result, json.dumps({"success": False, "successful_pauses": []}))

        # The failed pause must not have touched the still-running task
        self.assertEqual(self._task_statuses["222"], TaskStatus.IN_PROGRESS)

        # Let the service call finish normally to avoid unnecessary error prints in the end of the test
        goal_handle.get_result()

    def test_pause_service_backed_task_succeeds_if_it_finishes_within_grace_period(self) -> None:
        """A service-backed task that finishes on its own within the cancel_timeout grace period is reported as a
        successful pause, even though it actually ended up DONE rather than PAUSED.

        This lets e.g. a Mission that's blocked waiting on this subtask simply continue on to the next step, as if no
        pause had been requested.
        """
        goal_handle = self.start_add_two_ints_service_task(task_id="333", run_time_secs=1)
        self.wait_for_task_start("333")

        pause_response = self.execute_pause_task(task_ids=["333"])
        self.assertEqual(pause_response.result.task_status, TaskStatus.DONE)
        self.assertEqual(pause_response.result.task_result, json.dumps({"success": True, "successful_pauses": ["333"]}))

        self.assertEqual(goal_handle.get_result().result.task_status, TaskStatus.DONE)

    def test_pause_blocking_task_allows_new_blocking_task(self) -> None:
        """A paused blocking task must not block a new blocking task from starting, and must stay untouched by it."""
        goal_handle_1 = self.start_fibonacci_action_task("fibonacci_blocking", run_time_secs=3, task_id="111")
        self.wait_for_task_start("111")

        pause_response = self.execute_pause_task(task_ids=["111"])
        self.assertEqual(pause_response.result.task_status, TaskStatus.DONE)
        self.wait_for_task_status("111", TaskStatus.PAUSED)

        goal_handle_2 = self.start_fibonacci_action_task("fibonacci_blocking_2", run_time_secs=1, task_id="222")
        self.wait_for_task_start("222")
        self.assertEqual(goal_handle_2.get_result().result.task_status, TaskStatus.DONE)

        # The paused task must not have been cancelled by the new blocking task
        self.assertEqual(self._task_statuses["111"], TaskStatus.PAUSED)

        # Clean up the still-paused task
        cancel_response = self.execute_cancel_task(task_ids=["111"])
        self.assertEqual(cancel_response.result.task_status, TaskStatus.DONE)
        self.assertEqual(goal_handle_1.get_result().result.task_status, TaskStatus.CANCELED)

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
            goal_handle = self.start_fibonacci_action_task("fibonacci_non_cancelable", run_time_secs=3, task_id="111")
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
