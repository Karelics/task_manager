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

import time
import unittest
from copy import deepcopy
from unittest.mock import Mock, call, patch

# ROS
from rclpy.action import ActionClient
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

# Thirdparty
from task_manager_test_utils import TaskManagerTestNode, create_fibonacci_task_goal

# ROS messages
from action_msgs.msg import GoalStatus
from example_interfaces.action import Fibonacci
from example_interfaces.srv import AddTwoInts

# Task Manager messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import ActiveTask, ActiveTaskArray, TaskDoneResult, TaskStatus

# Current package
from task_manager.task_client import CancelTaskFailedError, TaskStartError

# pylint: disable=protected-access


class TestTaskManager(TaskManagerTestNode):
    """Main tests for the Task Manager by starting tasks from the execute_task service."""

    #########################
    # Test the behavior of the ExecuteTask Action Server
    #########################
    def test_execute_task_happy_flow(self) -> None:
        """Happy flow of starting the task that calls an existing service."""
        goal_handle = self.start_add_two_ints_service_task(task_id="123")
        result = goal_handle.get_result()

        expected_result = ExecuteTask.Result(
            task_id="123",
            task_status=TaskStatus.DONE,
            task_result='{"sum": 0}',
        )

        self.assertEqual(result.result, expected_result)

    def test_execute_task_cancel(self):
        """Task execution is cancelled by the Action Client in the middle of the task."""
        goal_handle = self.start_fibonacci_action_task(run_time_secs=5)

        goal_handle.cancel_goal()
        response = goal_handle.get_result()

        self.assertEqual(response.status, GoalStatus.STATUS_CANCELED)
        self.assertEqual(response.result.task_result, '{"sequence": []}')
        self.assertEqual(response.result.task_status, TaskStatus.CANCELED)

    def test_execute_task_cancel_failure(self):
        """Task execution is cancelled by the Action Client in the middle of the task, but the cancelling fails."""
        with patch("task_manager.task_client.ActionTaskClient.cancel_task", side_effect=CancelTaskFailedError):
            goal_handle = self.start_fibonacci_action_task(task_name="fibonacci_blocking", run_time_secs=5)
            goal_handle.cancel_goal()
            response = goal_handle.get_result()

        self.assertEqual(response.status, GoalStatus.STATUS_ABORTED)
        self.assertEqual(response.result.task_result, "")
        self.assertEqual(response.result.task_status, TaskStatus.IN_PROGRESS)
        self.assertEqual(response.result.error_code, response.result.ERROR_TASK_CANCEL_FAILED)

        # Properly cancel the goal by starting a new blocking task, to avoid printing a bunch of error logs
        goal_handle_2 = self.start_fibonacci_action_task(task_name="fibonacci_blocking", run_time_secs=0)
        goal_handle_2.get_result()

    def test_execute_task_cancel_called_externally(self):
        """Task execution is cancelled by an external source (normally CancelTask) in the middle of the task."""
        goal = create_fibonacci_task_goal(task_name="fibonacci", run_time_secs=5, task_id="123")
        goal_handle = self._start_task(goal)

        # It will take a while to register the task as an active task once the request has been sent.
        self.wait_for_task_start(task_id="123")
        task_client = self.task_manager_node.active_tasks._active_tasks["123"]

        task_client.cancel_task()
        response = goal_handle.get_result()

        self.assertEqual(response.status, GoalStatus.STATUS_ABORTED)
        self.assertEqual(response.result.task_result, '{"sequence": []}')
        self.assertEqual(response.result.task_status, TaskStatus.CANCELED)

    def execute_task_error_on_launch(self):
        """If the task errors during the validation steps before it even launches, make sure that the action result
        state is aborted and a task done result is published."""
        mock_publish = Mock()
        self.task_manager_node.results_pub.publish = mock_publish

        with patch("task_manager.task_manager_node.TaskManager._start_task", return_value=(None, "error")):
            goal = create_fibonacci_task_goal(run_time_secs=0, task_id="123")
            goal_handle = self._start_task(goal)
            response = goal_handle.get_result()

            expected_result = ExecuteTask.Result(
                task_id="123", task_result="{}", task_status="ERROR", error_code="error"
            )

            self.assertEqual(response.status, GoalStatus.STATUS_ABORTED)
            self.assertEqual(response.result, expected_result)
            mock_publish.assert_called_with(
                TaskDoneResult(
                    task_id="123", task_name="fibonacci", task_status="ERROR", source="CLOUD", task_result="{}"
                )
            )

    #########################
    # Test the general logic of the Tasks
    #########################

    def test_same_task_id_separately(self) -> None:
        """Test registering two tasks with the same id but at different times (allowed)."""
        goal_handle = self.start_add_two_ints_service_task()
        task_result_1 = goal_handle.get_result()
        self.assertEqual(task_result_1.result.task_status, TaskStatus.DONE)

        goal_handle_2 = self.start_add_two_ints_service_task()
        task_result_2 = goal_handle_2.get_result()
        self.assertEqual(task_result_2.result.task_status, TaskStatus.DONE)

    def test_same_task_id_concurrently(self) -> None:
        """Test registering two tasks with the same id at the same time (prohibited)."""
        goal_handle = self.start_add_two_ints_service_task(task_id="123", run_time_secs=1)
        goal_handle_2 = self.start_add_two_ints_service_task(task_id="123")
        response_1 = goal_handle.get_result()
        response_2 = goal_handle_2.get_result()

        self.assertEqual(response_1.result.task_status, TaskStatus.DONE)
        self.assertEqual(response_2.result.task_status, TaskStatus.ERROR)
        self.assertEqual(response_2.result.error_code, response_2.result.ERROR_DUPLICATE_TASK_ID)

    def test_task_start_user_inputs(self) -> None:
        """Test all the different execution paths for different user-given inputs."""
        with self.subTest("Non-existing task name"):
            goal_handle = self.start_add_two_ints_service_task(task_name="non-existing")
            response = goal_handle.get_result()

            self.assertEqual(response.result.task_status, TaskStatus.ERROR)
            self.assertEqual(response.result.error_code, response.result.ERROR_UNKNOWN_TASK)

        with self.subTest("Empty task name"):
            goal_handle = self.start_add_two_ints_service_task(task_name="")
            response = goal_handle.get_result()

            self.assertEqual(response.result.task_status, TaskStatus.ERROR)
            self.assertEqual(response.result.error_code, response.result.ERROR_UNKNOWN_TASK)

        with self.subTest("Invalid task type"):
            request = Fibonacci.Goal()
            goal_handle = self.start_add_two_ints_service_task(request=request)
            response = goal_handle.get_result()

            self.assertEqual(response.result.task_status, TaskStatus.ERROR)
            self.assertEqual(response.result.error_code, response.result.ERROR_TASK_DATA_PARSING_FAILED)

        with self.subTest("Invalid JSON"):
            goal = ExecuteTask.Goal(task_id="123", task_name="add_two_ints", task_data="{{}", source="")
            goal_handle = self._start_task(goal)
            response = goal_handle.get_result()

            self.assertEqual(response.result.task_status, TaskStatus.ERROR)
            self.assertEqual(response.result.error_code, response.result.ERROR_TASK_DATA_PARSING_FAILED)

        with self.subTest("Empty task_id"):
            goal_handle = self.start_add_two_ints_service_task(task_id="")
            response = goal_handle.get_result()
            self.assertTrue(response.result.task_id != "")
            self.assertEqual(response.result.task_status, TaskStatus.DONE)

    @patch("task_manager.task_registrator.ServiceTaskClient.start_task_async", side_effect=TaskStartError)
    def test_start_task_fails(self, _mock):
        """When starting of the task fails, error code is correctly added to response."""
        goal_handle = self.start_add_two_ints_service_task(task_id="")
        response = goal_handle.get_result()
        self.assertEqual(response.result.task_status, TaskStatus.ERROR)
        self.assertEqual(response.result.error_code, response.result.ERROR_TASK_START_ERROR)

    def test_blocking_task(self) -> None:
        """Test registering 2 different blocking tasks."""
        # Start a blocking Action Task
        goal_handle = self.start_fibonacci_action_task(task_name="fibonacci_blocking", run_time_secs=5, task_id="1")
        self.wait_for_task_start("1")

        # New blocking Service Task should cancel the previous task
        goal_handle_2 = self.start_add_two_ints_service_task(run_time_secs=0)

        response_1 = goal_handle.get_result()
        response_2 = goal_handle_2.get_result()

        self.assertEqual(response_1.result.task_status, TaskStatus.CANCELED)
        self.assertEqual(response_2.result.task_status, TaskStatus.DONE)

    def test_two_non_blocking_tasks(self) -> None:
        """Test registering 2 non-blocking tasks."""
        goal_handle = self.start_fibonacci_action_task("fibonacci", run_time_secs=1)
        goal_handle_2 = self.start_fibonacci_action_task("fibonacci_2", run_time_secs=1)
        result = goal_handle.get_result()
        result_2 = goal_handle_2.get_result()
        self.assertEqual(result.result.task_status, TaskStatus.DONE)
        self.assertEqual(result_2.result.task_status, TaskStatus.DONE)

    def test_blocking_task_cancelling_fails(self) -> None:
        """Sometimes blocking task cancelling might fail, for example if we try to cancel a service that executes for
        more than the cancel timeout."""
        self.task_manager_node.task_registrator.cancel_task_timeout = 0.1

        goal_handle_1 = self.start_add_two_ints_service_task(run_time_secs=1)

        # New blocking Service Task should cancel the previous task
        goal_handle_2 = self.start_add_two_ints_service_task(run_time_secs=1)

        result_1 = goal_handle_1.get_result()
        result_2 = goal_handle_2.get_result()

        self.assertEqual(result_1.result.task_status, TaskStatus.DONE)
        self.assertEqual(result_2.result.task_status, TaskStatus.ERROR)
        self.assertEqual(result_2.result.error_code, result_2.result.ERROR_TASK_START_ERROR)

    def test_same_task_twice(self) -> None:
        """Test requesting the same task again when the previous instance is still running."""
        with self.subTest("Action task, the first one should be cancelled"):
            goal_handle = self.start_fibonacci_action_task("fibonacci", run_time_secs=5, task_id="1")
            self.wait_for_task_start("1")
            goal_handle_2 = self.start_fibonacci_action_task("fibonacci", run_time_secs=0)

            result = goal_handle.get_result()
            self.assertEqual(result.result.task_status, TaskStatus.CANCELED)

            result_2 = goal_handle_2.get_result()
            self.assertEqual(result_2.result.task_status, TaskStatus.DONE)

        with self.subTest("Service Task, the second tasks waits for a while to see if the previous one finishes"):
            goal_handle_1 = self.start_add_two_ints_service_task(run_time_secs=1, task_id="5")
            goal_handle_2 = self.start_add_two_ints_service_task(run_time_secs=1, task_id="6")

            # Service tasks cannot be cancelled, they will be instead executed one after another.
            # Check that we truly have only one task executing after the two calls
            time.sleep(0.2)  # Sleep for a while to make sure that both tasks are started

            self.assertEqual(len(self.task_manager_node.active_tasks.get_active_tasks()), 1)

            response_1 = goal_handle_1.get_result()
            response_2 = goal_handle_2.get_result()

            self.assertEqual(response_1.result.task_status, TaskStatus.DONE)
            self.assertEqual(response_2.result.task_status, TaskStatus.DONE)

    def test_two_parallel_tasks(self) -> None:
        """Test cases for task's reentrant behavior.

        Checks that we can have multiple instances of the same task running at once in parallel.
        """
        cases = [
            {
                "case": "Happy flow: Two tasks with the same name will execute in parallel",
                "blocking": False,
                "number_of_active_tasks": 2,
            },
            {
                "case": "Tasks won't run in parallel if they are marked as blocking and reentrant",
                "blocking": True,
                "number_of_active_tasks": 1,
            },
        ]
        self.task_manager_node.known_tasks["add_two_ints"].reentrant = True

        for case in cases:
            with self.subTest(case["case"]):
                self.task_manager_node.known_tasks["add_two_ints"].blocking = case["blocking"]

                goal_handle_1 = self.start_add_two_ints_service_task(run_time_secs=1)
                goal_handle_2 = self.start_add_two_ints_service_task(run_time_secs=1)

                time.sleep(0.2)  # Sleep for a while to make sure that both tasks are registered
                self.assertEqual(
                    len(self.task_manager_node.active_tasks.get_active_tasks()), case["number_of_active_tasks"]
                )

                response_1 = goal_handle_1.get_result()
                response_2 = goal_handle_2.get_result()

                self.assertEqual(response_1.result.task_status, TaskStatus.DONE)
                self.assertEqual(response_2.result.task_status, TaskStatus.DONE)

    def test_register_multiple_blocking_tasks(self) -> None:
        """Tests that task registration correctly handles previous blocking task cancelling when new blocking task is
        given."""
        max_active_tasks = 0

        def active_tasks_changed_cb(active_tasks):
            nonlocal max_active_tasks
            max_active_tasks = max(max_active_tasks, len(active_tasks))
            if max_active_tasks > 2:
                self.task_manager_node.get_logger().error(
                    "Had more than 2 active blocking tasks simultaneously running!"
                )

        self.task_manager_node.active_tasks._active_tasks_changed_cb = active_tasks_changed_cb

        client = ActionClient(self.task_manager_node, ExecuteTask, "/task_manager/execute_task")
        client.wait_for_server(1)
        goal_1 = create_fibonacci_task_goal(task_name="fibonacci_blocking", run_time_secs=1)
        goal_2 = create_fibonacci_task_goal(task_name="fibonacci_blocking_2", run_time_secs=1)

        # Register multiple blocking tasks in fast succession
        for _ in range(10):
            # Start two tasks at the same time and make sure that we always have only one active task
            future_1 = client.send_goal_async(goal_1)
            future_2 = client.send_goal_async(goal_2)

            goal_handle_1 = self._get_response(future_1)
            goal_handle_2 = self._get_response(future_2)
            self.assertTrue(goal_handle_1.accepted)
            self.assertTrue(goal_handle_2.accepted)
            self.assertTrue(max_active_tasks < 2)

            # Execution order of these tasks is not guaranteed, so the end result might be DONE or CANCELED
            result_1 = goal_handle_1.get_result()
            result_2 = goal_handle_2.get_result()
            self.assertIn(result_1.result.task_status, [TaskStatus.DONE, TaskStatus.CANCELED])
            self.assertIn(result_2.result.task_status, [TaskStatus.DONE, TaskStatus.CANCELED])

    def test_active_tasks_publishing(self):
        """Tests that for a single task execution, active tasks will publish:

        - Empty active tasks list
        - Task in progress
        - Task Done
        - Empty active tasks list
        """
        mock_cb = Mock()
        transient_qos = QoSProfile(
            depth=10, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL
        )
        self.task_manager_node.create_subscription(
            ActiveTaskArray, "/task_manager/active_tasks", mock_cb, qos_profile=transient_qos
        )

        goal_handle = self.start_fibonacci_action_task("fibonacci", run_time_secs=1, task_id="1")
        goal_handle.get_result()

        expected_call_1 = ActiveTaskArray(
            active_tasks=[
                ActiveTask(
                    task_id="1",
                    task_name="fibonacci",
                    task_status=str(TaskStatus.IN_PROGRESS),
                    source="CLOUD",
                )
            ]
        )
        expected_call_2 = deepcopy(expected_call_1)
        expected_call_2.active_tasks[0].task_status = str(TaskStatus.DONE)

        # Wait just a bit so that the active tasks sub also gets the final messages
        _wait_for_calls(
            mock_object=mock_cb,
            calls=[call(ActiveTaskArray()), call(expected_call_1), call(expected_call_2), call(ActiveTaskArray())],
            timeout=0.5,
        )

    def test_report_cancel_as_success(self):
        """If set to True, Action Server's CANCELED state will be reported as SUCCESS instead."""
        self.task_manager_node.known_tasks["fibonacci"].cancel_reported_as_success = True
        goal_handle = self.start_fibonacci_action_task("fibonacci", run_time_secs=1)
        goal_handle.cancel_goal()
        response = goal_handle.get_result()
        self.assertEqual(response.result.task_status, TaskStatus.DONE)

    def test_call_tasks_action_server(self):
        """Test calling the direct action server that a task exposes."""
        action_topic = f"{self.task_manager_node.task_registrator.TASK_TOPIC_PREFIX}/fibonacci"
        client = ActionClient(self.task_manager_node, Fibonacci, action_topic)
        client.wait_for_server(5)
        result = client.send_goal(Fibonacci.Goal(order=0))

        self.assertEqual(result.status, GoalStatus.STATUS_SUCCEEDED)
        self.assertEqual(result.result, Fibonacci.Result(sequence=[0, 1]))

    def test_call_tasks_service_server(self):
        """Test calling the direct service server that a task exposes."""
        service_topic = f"{self.task_manager_node.task_registrator.TASK_TOPIC_PREFIX}/add_two_ints"
        client = self.task_manager_node.create_client(AddTwoInts, service_topic)
        client.wait_for_service(timeout_sec=1)

        response = client.call(AddTwoInts.Request(a=1, b=0))
        expected_response = AddTwoInts.Response(sum=1)
        self.assertEqual(response, expected_response)


def _wait_for_calls(mock_object, calls, timeout=0.5):
    """Waits for the mock object to be called with given calls."""
    timeout = time.time() + timeout
    while time.time() <= timeout:
        try:
            mock_object.assert_has_calls(calls=calls)
        except AssertionError:
            pass
        time.sleep(1 / 100)
    mock_object.assert_has_calls(calls=calls)


if __name__ == "__main__":
    unittest.main()
