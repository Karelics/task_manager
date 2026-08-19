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

import threading
import unittest
from unittest.mock import Mock, patch

# ROS
from rclpy.node import Node
from rclpy.task import Future

# Thirdparty
from rosbridge_library.internal.message_conversion import extract_values

# ROS messages
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from example_interfaces.action import Fibonacci

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.task_client import (
    ActionTaskClient,
    CancelTaskFailedError,
    PauseTaskFailedError,
    ResumeTaskFailedError,
    ServiceTaskClient,
    TaskStartError,
)
from task_manager.task_details import TaskDetails
from task_manager.task_specs import TaskServerType, TaskSpecs

# pylint: disable=protected-access


class TestActionTaskClient(unittest.TestCase):
    """Unit tests for ActionTaskClient.

    Most of the functionality is tested with integration tests
    """

    def setUp(self) -> None:
        self.cb_called = False

    def _done_cb(self, _task_specs, _task_details) -> None:
        self.cb_called = True

    def test_done_cb_normal(self) -> None:
        """Test normal execution of the goal_done_cb."""
        task_client = get_action_task_client("task_1")
        task_client.register_done_callback(self._done_cb)
        goal_future = Future(executor=Mock())
        goal_future._result = Fibonacci.Impl.GetResultService.Response(
            status=GoalStatus.STATUS_SUCCEEDED, result=Fibonacci.Result(sequence=[0, 1])
        )

        task_client._goal_done_cb(goal_future)

        self.assertTrue(self.cb_called)
        self.assertEqual(task_client.task_details.status, TaskStatus.DONE)

        # Getting result.sequence produces weird output: "array('i', [0, 1])", while we expect "[0, 1]"
        # Seems to be a known issue https://github.com/ros2/demos/issues/388
        # As a workaround, use extract_values-function to get the result in json format
        result = extract_values(task_client.task_details.result)
        self.assertEqual(result, {"sequence": [0, 1]}, msg=str(task_client.task_details.result))

    def test_done_cb_bad_status(self) -> None:
        """Test goal_done_cb when the goal status is not valid."""
        task_client = get_action_task_client("task_1")
        task_client.register_done_callback(self._done_cb)
        goal_future = Future(executor=Mock())
        goal_future._result = Fibonacci.Impl.GetResultService.Response(status=-1, result=Fibonacci.Result())

        task_client._goal_done_cb(goal_future)

        self.assertTrue(self.cb_called)
        self.assertEqual(task_client.task_details.status, TaskStatus.ERROR)

        # Getting result.sequence produces weird output: "array('i', [0, 1])", while we expect "[0, 1]"
        # Seems to be a known issue https://github.com/ros2/demos/issues/388
        # As a workaround, use extract_values-function to get the result in json format
        result = extract_values(task_client.task_details.result)
        self.assertEqual(result, {"sequence": []}, msg=str(task_client.task_details.result))

    def test_cancel_task_no_goal_handle(self):
        """Tests that we do not crash if goal handle does not exist."""
        task_client = get_action_task_client("task_1")
        self.assertRaises(CancelTaskFailedError, task_client.cancel_task)

    def test_request_canceling_no_goal_handle(self):
        """Tests that we do not crash if goal handle does not exist."""
        task_client = get_action_task_client("task_1")
        self.assertRaises(CancelTaskFailedError, task_client.request_canceling)

    @patch("task_manager.task_client.ActionTaskClient._request_canceling")
    def test_cancel_task_no_result_future(self, mock_request_canceling: Mock):
        """Tests that we do not crash if result future does not exist."""
        cases = [
            {"case": "Unknown goal id", "return_code": CancelGoal.Response.ERROR_UNKNOWN_GOAL_ID},
            {"case": "Goal terminated", "return_code": CancelGoal.Response.ERROR_GOAL_TERMINATED},
        ]

        task_client = get_action_task_client("task_1")
        task_client._goal_handle = Mock()
        for case in cases:
            mock_request_canceling.return_value = CancelGoal.Response(return_code=case["return_code"])
            with self.subTest(case["case"]):
                self.assertRaises(CancelTaskFailedError, task_client.cancel_task)

    def test_cancel_task_short_circuits_when_paused(self):
        """Cancelling an already-paused task must finish it directly, without touching the (gone) goal handle."""
        task_client = get_action_task_client("task_1")
        task_client.register_done_callback(self._done_cb)
        # `_paused` (not the public status) is the source of truth for "this client's own goal was really
        # cancelled by pause_task()" - see _finish_if_paused()'s docstring for why.
        task_client._paused = True
        task_client.task_details.status = TaskStatus.PAUSED

        task_client.cancel_task()

        self.assertTrue(self.cb_called)
        self.assertEqual(task_client.task_details.status, TaskStatus.CANCELED)
        self.assertTrue(task_client.goal_done)

    def test_request_canceling_short_circuits_when_paused(self):
        """Request_canceling() alone (without going through cancel_task()) must also finish an already-paused task.

        directly - callers like ParallelTask.cancel_async() rely on this to never special-case PAUSED themselves.
        """
        task_client = get_action_task_client("task_1")
        task_client.register_done_callback(self._done_cb)
        task_client._paused = True
        task_client.task_details.status = TaskStatus.PAUSED

        task_client.request_canceling()

        self.assertTrue(self.cb_called)
        self.assertEqual(task_client.task_details.status, TaskStatus.CANCELED)
        self.assertTrue(task_client.goal_done)

    @patch.object(ActionTaskClient, "request_canceling")
    def test_cancel_task_does_not_short_circuit_a_composite_forced_into_paused_status(self, mock_request_canceling):
        """A composite task's (Mission/ParallelTaskExecutor) own status can be forced to PAUSED purely for display by
        system_tasks.py's `_sync_composite_statuses()`, without its real goal ever being paused.

        cancel_task() must not trust that display-only status and must still cancel the real, still-live goal.
        """
        task_client = get_action_task_client("task_1")
        task_client.register_done_callback(self._done_cb)
        task_client.task_details.status = TaskStatus.PAUSED  # forced externally, _paused was never set
        task_client._goal_handle = Mock(status=GoalStatus.STATUS_EXECUTING)
        task_client._goal_done.set()  # pretend the cancel completed instantly - only the dispatch is under test

        task_client.cancel_task()

        # Took the real cancel path (request_canceling() against the live goal_handle), not the paused shortcut -
        # which would never call request_canceling() and would fire the done callback synchronously instead.
        mock_request_canceling.assert_called_once()
        self.assertFalse(self.cb_called)

    def test_pause_task_no_goal_handle(self):
        """Tests that we do not crash if goal handle does not exist."""
        task_client = get_action_task_client("task_1")
        self.assertRaises(PauseTaskFailedError, task_client.pause_task)

    def test_pause_task_already_paused(self):
        """Tests that pausing an already-paused task raises."""
        task_client = get_action_task_client("task_1")
        task_client.task_details.status = TaskStatus.PAUSED
        self.assertRaises(PauseTaskFailedError, task_client.pause_task)

    def test_pause_task_already_finished(self):
        """Tests that pausing a finished task raises."""
        task_client = get_action_task_client("task_1")
        task_client._goal_done.set()
        self.assertRaises(PauseTaskFailedError, task_client.pause_task)

    def test_resume_task_noop_when_not_paused(self):
        """Resuming a task that isn't paused is a no-op success."""
        task_client = get_action_task_client("task_1")
        task_client.resume_task()
        self.assertIsNone(task_client._goal_handle)

    def test_pause_captures_partial_result(self):
        """The Result the server attaches to the pause-cancel must be stored, without any "task done" side effects."""
        task_client = get_action_task_client("task_1")
        task_client.register_done_callback(self._done_cb)
        task_client._pausing = True
        goal_future = Future(executor=Mock())
        goal_future._result = Fibonacci.Impl.GetResultService.Response(
            status=GoalStatus.STATUS_CANCELED, result=Fibonacci.Result(sequence=[0, 1, 1])
        )

        task_client._goal_done_cb(goal_future)

        self.assertTrue(task_client._pause_done.is_set())
        self.assertFalse(self.cb_called)
        self.assertFalse(task_client.goal_done)
        self.assertEqual(len(task_client._paused_results), 1)
        self.assertEqual(extract_values(task_client._paused_results[0]), {"sequence": [0, 1, 1]})

    def test_final_result_concatenates_paused_segments(self):
        """Fields listed in result_concat_fields are concatenated across paused segments and the final segment, in
        chronological order."""
        task_client = get_concat_action_task_client(result_concat_fields=["sequence"])
        task_client._paused_results = [Fibonacci.Result(sequence=[0, 1]), Fibonacci.Result(sequence=[1, 2])]
        goal_future = Future(executor=Mock())
        goal_future._result = Fibonacci.Impl.GetResultService.Response(
            status=GoalStatus.STATUS_SUCCEEDED, result=Fibonacci.Result(sequence=[3, 5])
        )

        task_client._goal_done_cb(goal_future)

        self.assertEqual(task_client.task_details.status, TaskStatus.DONE)
        result = extract_values(task_client.task_details.result)
        self.assertEqual(result, {"sequence": [0, 1, 1, 2, 3, 5]})

    def test_final_result_without_concat_fields_keeps_final_segment_only(self):
        """With no result_concat_fields configured, paused segments' results are discarded as before."""
        task_client = get_concat_action_task_client(result_concat_fields=[])
        task_client._paused_results = [Fibonacci.Result(sequence=[0, 1])]
        goal_future = Future(executor=Mock())
        goal_future._result = Fibonacci.Impl.GetResultService.Response(
            status=GoalStatus.STATUS_SUCCEEDED, result=Fibonacci.Result(sequence=[3, 5])
        )

        task_client._goal_done_cb(goal_future)

        self.assertEqual(extract_values(task_client.task_details.result), {"sequence": [3, 5]})

    def test_final_result_skips_bad_concat_field(self):
        """A configured field that can't be concatenated is skipped without breaking the rest of the result."""
        task_client = get_concat_action_task_client(result_concat_fields=["no_such_field", "sequence"])
        task_client._paused_results = [Fibonacci.Result(sequence=[0, 1])]
        goal_future = Future(executor=Mock())
        goal_future._result = Fibonacci.Impl.GetResultService.Response(
            status=GoalStatus.STATUS_SUCCEEDED, result=Fibonacci.Result(sequence=[3, 5])
        )

        task_client._goal_done_cb(goal_future)

        self.assertEqual(task_client.task_details.status, TaskStatus.DONE)
        self.assertEqual(extract_values(task_client.task_details.result), {"sequence": [0, 1, 3, 5]})

    def test_cancel_while_paused_reports_merged_paused_segments(self):
        """Cancelling a paused task must report the merged partial results instead of an empty Result."""
        task_client = get_concat_action_task_client(result_concat_fields=["sequence"])
        task_client._paused = True
        task_client.task_details.status = TaskStatus.PAUSED
        task_client._paused_results = [Fibonacci.Result(sequence=[0, 1]), Fibonacci.Result(sequence=[1, 2])]

        task_client.cancel_task()

        self.assertEqual(task_client.task_details.status, TaskStatus.CANCELED)
        self.assertEqual(extract_values(task_client.task_details.result), {"sequence": [0, 1, 1, 2]})

    def test_resume_failure_reports_merged_paused_segments(self):
        """A paused task whose restart fails must still report the merged partial results."""
        task_client = get_concat_action_task_client(result_concat_fields=["sequence"])
        task_client._paused = True
        task_client._paused_results = [Fibonacci.Result(sequence=[0, 1])]

        with patch.object(ActionTaskClient, "start_task_async", side_effect=TaskStartError("server gone")):
            self.assertRaises(ResumeTaskFailedError, task_client.resume_task)

        self.assertTrue(task_client.goal_done)
        self.assertEqual(extract_values(task_client.task_details.result), {"sequence": [0, 1]})


class ServiceTaskClientUnittests(unittest.TestCase):
    """Unittests for ServiceTaskClient.

    Most of the functionality is tested with integration tests.
    """

    def test_service_success_field(self):
        """Task's service_success_field sets the final task status correctly."""
        task_client = ServiceTaskClient(
            node=Mock(), task_details=Mock(), task_specs=Mock(service_success_field="success"), service_clients={}
        )

        mock_future = Mock()
        mock_future.result.return_value = Mock(success=False)
        task_client._done_callback(future=mock_future)
        self.assertEqual(task_client.task_details.status, TaskStatus.ERROR)

    def test_pause_task_already_finished_raises(self):
        """Pausing a service-backed task that has already finished raises straight away."""
        task_specs = Mock(cancel_timeout=1.0)
        task_client = ServiceTaskClient(node=Mock(), task_details=Mock(), task_specs=task_specs, service_clients={})
        task_client._goal_done.set()
        self.assertRaises(PauseTaskFailedError, task_client.pause_task)

    def test_pause_task_raises_if_service_does_not_finish_within_grace_period(self):
        """Pausing waits out cancel_timeout for the service to finish naturally, and only fails if it's still running
        afterwards."""
        task_specs = Mock(cancel_timeout=0.05)
        task_client = ServiceTaskClient(node=Mock(), task_details=Mock(), task_specs=task_specs, service_clients={})
        self.assertRaises(PauseTaskFailedError, task_client.pause_task)

    def test_pause_task_succeeds_if_service_finishes_within_grace_period(self):
        """If the service call finishes naturally while pause_task() is waiting it out, the pause does not raise - it's
        treated as if it succeeded, letting a Mission continue to its next step."""
        task_specs = Mock(cancel_timeout=1.0)
        task_client = ServiceTaskClient(node=Mock(), task_details=Mock(), task_specs=task_specs, service_clients={})
        threading.Timer(0.05, task_client._goal_done.set).start()

        task_client.pause_task()  # Must not raise

    def test_resume_task_is_noop(self):
        """Service-backed tasks are never paused, so resuming one is a no-op."""
        task_client = ServiceTaskClient(node=Mock(), task_details=Mock(), task_specs=Mock(), service_clients={})
        task_client.resume_task()


def get_action_task_client(
    task_name: str,
) -> ActionTaskClient:
    """Initializes and returns a ActionTaskClient with minimal info for testing purposes."""
    task_details = TaskDetails(
        task_id="1",
        source="CLOUD",
        status=TaskStatus.RECEIVED,
    )
    return ActionTaskClient(
        Mock(spec=Node), task_details, task_specs=Mock(task_name=task_name), action_clients={task_name: Mock()}
    )


def get_concat_action_task_client(result_concat_fields) -> ActionTaskClient:
    """ActionTaskClient with a real Fibonacci-backed TaskSpecs, for the paused-segment result concatenation tests."""
    task_details = TaskDetails(
        task_id="1",
        source="CLOUD",
        status=TaskStatus.IN_PROGRESS,
    )
    task_specs = TaskSpecs(
        task_name="fibonacci",
        topic="/fibonacci",
        msg_interface=Fibonacci,
        task_server_type=TaskServerType.ACTION,
        result_concat_fields=result_concat_fields,
    )
    return ActionTaskClient(Mock(spec=Node), task_details, task_specs=task_specs, action_clients={"fibonacci": Mock()})


if __name__ == "__main__":
    unittest.main()
