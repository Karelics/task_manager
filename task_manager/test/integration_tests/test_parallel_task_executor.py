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
from typing import Any, Dict

# Thirdparty
from task_manager_test_utils import TaskManagerTestNode

# Task Manager messages
from task_manager_msgs.action import PerformInParallel
from task_manager_msgs.msg import SubtaskGoal, TaskStatus


class TestParallelTaskExecutor(TaskManagerTestNode):
    """Tests the ParallelTaskExecutor task which executes multiple tasks in parallel."""

    def test_parallel_tasks(self):
        """When one task finishes, the others should be canceled and the PerformInParallel task should finish with DONE
        status."""
        goal = PerformInParallel.Goal(
            subtasks=[
                SubtaskGoal(task_id="fib", task_name="fibonacci", task_data='{"order": 1}'),
                SubtaskGoal(task_id="wait", task_name="system/wait", task_data='{"duration": 3.0}'),
            ]
        )

        goal_handle = self.run_parallel_tasks(goal)
        response = goal_handle.get_result()

        subtask_results = self.parse_task_results(response.result.task_result)
        self.assertEqual(response.result.task_status, TaskStatus.DONE)
        self.assertEqual(subtask_results["fib"]["task_status"], TaskStatus.DONE)
        self.assertEqual(subtask_results["wait"]["task_status"], TaskStatus.CANCELED)

    def test_parallel_task_stop(self):
        """When stopped all the tasks should be canceled."""
        goal = PerformInParallel.Goal(
            subtasks=[
                SubtaskGoal(task_id="fib", task_name="fibonacci", task_data='{"order": 20}'),
                SubtaskGoal(task_id="wait", task_name="system/wait", task_data='{"duration": 30.0}'),
            ]
        )

        goal_handle = self.run_parallel_tasks(goal)
        stop_response = self.execute_stop_task()
        response = goal_handle.get_result()

        subtask_results = self.parse_task_results(response.result.task_result)
        self.assertEqual(stop_response.result.task_status, TaskStatus.DONE)
        self.assertEqual(response.result.task_status, TaskStatus.CANCELED)
        self.assertEqual(subtask_results["fib"]["task_status"], TaskStatus.CANCELED)
        self.assertEqual(subtask_results["wait"]["task_status"], TaskStatus.CANCELED)

    def test_parallel_task_task_error(self):
        """The Fibonacci task returns an error -> The PerformInParallel task should return an error as well."""
        goal = PerformInParallel.Goal(
            subtasks=[
                SubtaskGoal(task_id="fib", task_name="fibonacci", task_data='{"order": -1}'),
                SubtaskGoal(task_id="wait", task_name="system/wait", task_data='{"duration": 30.0}'),
            ]
        )

        goal_handle = self.run_parallel_tasks(goal)
        response = goal_handle.get_result()

        subtask_results = self.parse_task_results(response.result.task_result)
        self.assertEqual(response.result.task_status, TaskStatus.ERROR)
        self.assertEqual(subtask_results["fib"]["task_status"], TaskStatus.ERROR)
        self.assertEqual(subtask_results["wait"]["task_status"], TaskStatus.CANCELED)

    def test_error_in_task_start(self):
        """Causing an error in the start_task by setting both tasks to have the same task_id."""
        goal = PerformInParallel.Goal(
            subtasks=[
                SubtaskGoal(task_id="fib", task_name="fibonacci", task_data='{"order": 10}'),
                SubtaskGoal(task_id="fib", task_name="system/wait", task_data='{"duration": 30.0}'),
            ]
        )

        goal_handle = self.run_parallel_tasks(goal)
        response = goal_handle.get_result()

        subtask_results = self.parse_task_results(response.result.task_result)
        self.assertEqual(response.result.task_status, TaskStatus.ERROR)
        self.assertEqual(subtask_results["fib"]["task_status"], TaskStatus.ERROR)

    def test_cancel_timeout(self):
        """Tests handling of the cancel timeout.

        Calling add_two_ints service with a sum which is higher than the cancel timeout since the service is built to
        sleep the sum of the integers seconds.
        """
        goal = PerformInParallel.Goal(
            subtasks=[
                SubtaskGoal(task_id="add", task_name="add_two_ints_non_blocking", task_data='{"a": 10, "b": 10}'),
                SubtaskGoal(task_id="wait", task_name="system/wait", task_data='{"duration": 1.0}'),
            ]
        )

        goal_handle = self.run_parallel_tasks(goal)
        response = goal_handle.get_result()

        subtask_results = self.parse_task_results(response.result.task_result)
        self.assertEqual(response.result.task_status, TaskStatus.ERROR)
        self.assertEqual(subtask_results["add"]["task_status"], TaskStatus.IN_PROGRESS)
        self.assertEqual(subtask_results["wait"]["task_status"], TaskStatus.DONE)

    def test_cancel_timeout_allowed_to_continue(self):
        """If the task is allowed to continue, the PerformInParallel task should finish with DONE status even if the
        cancel does not succeed within the timeout."""
        goal = PerformInParallel.Goal(
            subtasks=[
                SubtaskGoal(task_id="fib", task_name="add_two_ints_non_blocking", task_data='{"a": 3, "b": 3}'),
                SubtaskGoal(task_id="wait", task_name="system/wait", task_data='{"duration": 1.0}'),
            ]
        )
        self.task_manager_node.known_tasks["add_two_ints_non_blocking"].require_finish_on_parallel_cancel = False

        goal_handle = self.run_parallel_tasks(goal)
        response = goal_handle.get_result()

        subtask_results = self.parse_task_results(response.result.task_result)
        self.assertEqual(response.result.task_status, TaskStatus.DONE)
        self.assertEqual(subtask_results["fib"]["task_status"], TaskStatus.DONE)
        self.assertEqual(subtask_results["wait"]["task_status"], TaskStatus.DONE)

    def test_tasks_without_ids(self):
        """If the tasks do not have task_ids, they should be automatically assigned unique task_ids."""
        goal = PerformInParallel.Goal(
            subtasks=[
                SubtaskGoal(task_id="", task_name="fibonacci", task_data='{"order": 1}'),
                SubtaskGoal(task_id="", task_name="system/wait", task_data='{"duration": 1.0}'),
            ]
        )

        goal_handle = self.run_parallel_tasks(goal)
        response = goal_handle.get_result()

        subtask_results = self.parse_task_results(response.result.task_result)
        self.assertEqual(response.result.task_status, TaskStatus.DONE)
        for _, subtask_res in subtask_results.items():
            self.assertNotEqual(subtask_res["task_id"], "")

    def test_single_subtask(self):
        """If there is only one subtask, the PerformInParallel task should finish with the same status as the
        subtask."""
        goal = PerformInParallel.Goal(
            subtasks=[SubtaskGoal(task_id="fib", task_name="fibonacci", task_data='{"order": 1}')]
        )

        goal_handle = self.run_parallel_tasks(goal)
        response = goal_handle.get_result()

        subtask_results = self.parse_task_results(response.result.task_result)
        self.assertEqual(response.result.task_status, TaskStatus.DONE)
        self.assertEqual(subtask_results["fib"]["task_status"], TaskStatus.DONE)

    def test_no_subtasks(self):
        """If there are no subtasks, the PerformInParallel task should finish with DONE status."""
        goal = PerformInParallel.Goal(subtasks=[])

        goal_handle = self.run_parallel_tasks(goal)
        response = goal_handle.get_result()

        self.assertEqual(response.result.task_status, TaskStatus.DONE)

    @staticmethod
    def parse_task_results(task_results: str) -> Dict[str, Dict[str, Any]]:
        """Parse the task results from a JSON string to a dictionary with task_id as key and the result as value."""
        parsed_result = json.loads(task_results)
        results: Dict[str, Dict[str, Any]] = {}
        for result in parsed_result["results"]:
            results[result["task_id"]] = result
        print(f"Parsed task results: {results}")
        return results
