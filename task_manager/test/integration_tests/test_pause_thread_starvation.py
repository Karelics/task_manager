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
from typing import List

# ROS
from rclpy.action.client import ClientGoalHandle

# Thirdparty
from task_manager_test_utils import TaskManagerTestNode

# Task Manager messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import TaskStatus

# Every fibonacci task declared by TaskManagerTestNode that accepts cancelling, i.e. can be paused. Distinct task
# names are needed, since starting a non-reentrant task cancels an existing (also paused) task of the same name.
PAUSABLE_TASK_NAMES = [
    "fibonacci",
    "fibonacci_2",
    "fibonacci_blocking",
    "fibonacci_blocking_2",
    "fibonacci_cancel_on_stop",
]


class PauseThreadStarvationTests(TaskManagerTestNode):
    """Regression tests for the pause task hanging when the executor runs out of threads."""

    EXECUTOR_NUM_THREADS = 6

    # Generous upper limit for a single request. A healthy pause takes well below a second.
    REQUEST_TIMEOUT = 15.0

    def _get_result_with_timeout(self, goal_handle: ClientGoalHandle) -> ExecuteTask.Result:
        """Like goal_handle.get_result(), but fails the test instead of blocking forever."""
        return self._get_response(goal_handle.get_result_async(), timeout=self.REQUEST_TIMEOUT).result

    def _pause_with_timeout(self, task_ids: List[str]) -> ExecuteTask.Result:
        """Like execute_pause_task(), but fails the test instead of blocking forever if the pause hangs."""
        pause_goal = ExecuteTask.Goal(
            task_name="system/pause_task", task_data=json.dumps({"paused_tasks": task_ids}), source=""
        )
        return self._get_result_with_timeout(self._start_task(pause_goal))

    def test_pausing_many_tasks_does_not_starve_executor(self) -> None:
        """Pausing tasks one after another must keep working, no matter how many tasks are already paused."""
        goal_handles = []
        for i, task_name in enumerate(PAUSABLE_TASK_NAMES):
            task_id = f"paused_{i}"
            goal_handles.append(self.start_fibonacci_action_task(task_name, run_time_secs=30, task_id=task_id))
            self.wait_for_task_start(task_id)

            pause_result = self._pause_with_timeout([task_id])
            self.assertEqual(
                pause_result.task_status,
                TaskStatus.DONE,
                f"Pausing task number {i + 1} failed while {i} task(s) were already paused: {pause_result.task_result}",
            )
            self.wait_for_task_status(task_id, TaskStatus.PAUSED)

        # Task Manager must still respond to new requests while all of the tasks are paused
        unknown_pause_result = self._pause_with_timeout(["non_existing_task_id"])
        self.assertEqual(unknown_pause_result.task_status, TaskStatus.ERROR)

        # Clean up the paused tasks
        cancel_result = self._get_result_with_timeout(
            self._start_task(
                ExecuteTask.Goal(
                    task_name="system/cancel_task",
                    task_data=json.dumps({"cancelled_tasks": [f"paused_{i}" for i in range(len(goal_handles))]}),
                    source="",
                )
            )
        )
        self.assertEqual(cancel_result.task_status, TaskStatus.DONE)
        for goal_handle in goal_handles:
            self.assertEqual(self._get_result_with_timeout(goal_handle).task_status, TaskStatus.CANCELED)


if __name__ == "__main__":
    unittest.main()
