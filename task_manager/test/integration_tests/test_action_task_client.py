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
from time import sleep

# ROS
import rclpy
from rclpy.action import CancelResponse, GoalResponse
from rclpy.action.client import ClientGoalHandle
from rclpy.executors import MultiThreadedExecutor

# Thirdparty
from mock_servers import create_fib_action_server

# ROS messages
from example_interfaces.action import Fibonacci

# Task Manager messages
from task_manager_msgs.msg import TaskStatus

# Task Manager
from task_manager.task_client import ActionTaskClient, CancelTaskFailedError, TaskStartError
from task_manager.task_details import TaskDetails
from task_manager.task_specs import TaskServerType, TaskSpecs

# pylint: disable=duplicate-code, protected-access
# The init is very similar to test_service_task_client, but it is fine in this case


class TestActionTaskClient(unittest.TestCase):
    """Integration tests for ActionTaskClient."""

    def setUp(self) -> None:
        rclpy.init()

        self._node = rclpy.create_node("test_node")
        self.fibonacci_server = create_fib_action_server(self._node, "fibonacci")
        self.executor = MultiThreadedExecutor()

        self.executor.add_node(self._node)
        self.spin_thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.spin_thread.start()

        self._task_specs = TaskSpecs(
            task_name="fibonacci",
            blocking=True,
            cancel_on_stop=True,
            topic="/fibonacci",
            cancel_reported_as_success=False,
            msg_interface=Fibonacci,
            reentrant=True,
            task_server_type=TaskServerType.ACTION,
        )

        self._task_details = TaskDetails(
            task_id="",
            source="",
            status=TaskStatus.RECEIVED,
        )

    def tearDown(self) -> None:
        self._node.destroy_node()
        rclpy.try_shutdown()
        self.spin_thread.join()

    def test_start_task_async_happy_flow(self):
        """Happy flow of starting the task."""
        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.start_task_async(Fibonacci.Goal(order=1))

        self.assertEqual(client.task_details.status, TaskStatus.IN_PROGRESS)
        client.goal_done.wait(timeout=5)
        self.assertEqual(client.task_details.status, TaskStatus.DONE)
        self.assertEqual(client.task_details.result, Fibonacci.Result(sequence=[0, 1]))

    def test_start_task_async_server_not_available(self):
        """Action server is not available when starting a task."""
        self._task_specs.topic = "non_existing"
        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.server_wait_timeout = 0.2
        with self.assertRaises(TaskStartError):
            client.start_task_async(Fibonacci.Goal(order=1))
        self.assertEqual(client.task_details.status, TaskStatus.ERROR)

    def test_start_task_async_server_wait_timeout(self):
        """Action server doesn't accept the goal within a given time limit when starting a task."""

        def sleep_goal_callback(_):
            """Makes the Action Server's goal_callback to hang, eventually timing out the task start."""
            sleep(1.0)
            return GoalResponse.ACCEPT

        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.server_wait_timeout = 0.2
        self.fibonacci_server.register_goal_callback(sleep_goal_callback)
        with self.assertRaises(TaskStartError):
            client.start_task_async(Fibonacci.Goal(order=1))
        self.assertEqual(client.task_details.status, TaskStatus.ERROR)
        client.goal_done.wait(5)

    def test_start_task_async_server_not_accepting_goal(self):
        """Action server rejects the goal when trying to start the task."""

        def reject_goal_callback(_):
            """Instantly rejects the goal."""
            return GoalResponse.REJECT

        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.server_wait_timeout = 0.2
        self.fibonacci_server.register_goal_callback(reject_goal_callback)
        with self.assertRaises(TaskStartError):
            client.start_task_async(Fibonacci.Goal(order=1))
        self.assertEqual(client.task_details.status, TaskStatus.ERROR)

    def test_cancel_task_happy_flow(self):
        """Task cancel passes successfully."""
        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.start_task_async(Fibonacci.Goal(order=1))
        client.cancel_task()
        self.assertEqual(client.task_details.status, TaskStatus.CANCELED)

    def test_cancel_task_rejected(self):
        """Action server rejects the cancel request, while trying to cancel a task."""

        def reject_cancel_callback(_):
            return CancelResponse.REJECT

        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        self.fibonacci_server.register_cancel_callback(reject_cancel_callback)
        client.start_task_async(Fibonacci.Goal(order=5))
        with self.assertRaises(CancelTaskFailedError):
            client.cancel_task()
        self.assertEqual(client.task_details.status, TaskStatus.IN_PROGRESS)

        # Finally, cancel the goal to avoid unnecessary error prints in the end of the test
        def accept_cancel_callback(_):
            return CancelResponse.ACCEPT

        self.fibonacci_server.register_cancel_callback(accept_cancel_callback)
        client.cancel_task()

    def test_cancel_accept_task_takes_too_long(self):
        """Action doesn't accept the cancellation within a given time limit."""

        def sleep_cancel_callback(_):
            sleep(0.5)
            return CancelResponse.ACCEPT

        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.cancel_task_timeout = 0.2

        self.fibonacci_server.register_cancel_callback(sleep_cancel_callback)
        client.start_task_async(Fibonacci.Goal(order=5))
        with self.assertRaises(CancelTaskFailedError):
            client.cancel_task()
        self.assertEqual(client.task_details.status, TaskStatus.IN_PROGRESS)

        # Finally, wait for the goal to finish to avoid error logs
        client.goal_done.wait(timeout=5)

    def test_cancel_task_takes_too_long(self):
        """Action takes too long to actually cancel the task."""

        def execute_cb(_goal_handle):
            """Sleeps for 1 second and doesn't react to cancel commands."""
            sleep(1)
            return Fibonacci.Result()

        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.cancel_task_timeout = 0.2
        self.fibonacci_server.register_execute_callback(execute_cb)
        client.start_task_async(Fibonacci.Goal(order=0))
        with self.assertRaises(CancelTaskFailedError):
            client.cancel_task()

        self.assertEqual(client.task_details.status, TaskStatus.IN_PROGRESS)
        # Finally, wait for the goal to finish to avoid error logs
        client.goal_done.wait(timeout=5)

    def test_cancel_task_goal_not_known_by_server(self) -> None:
        """Action server doesn't know the goal when trying to cancel it."""
        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.start_task_async(Fibonacci.Goal(order=1))
        self.fibonacci_server.destroy()
        self.fibonacci_server = create_fib_action_server(self._node, "fibonacci")
        client.cancel_task()
        self.assertEqual(client.task_details.status, TaskStatus.CANCELED)

    def test_goal_cb_called_only_once(self) -> None:
        """Checks that goal_cb is ran only once per goal and we do not throw error for the cancel."""

        def execute_cb(_goal_handle):
            _goal_handle.succeed()
            return Fibonacci.Result()

        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        self.fibonacci_server.register_execute_callback(execute_cb)
        client.start_task_async(Fibonacci.Goal(order=1))
        client.goal_done.wait(timeout=1)
        handle = ClientGoalHandle(
            goal_id=client._goal_handle.goal_id,  # type: ignore[union-attr]
            action_client=client._client,
            goal_response=Fibonacci.Result,
        )
        client._goal_handle = handle
        client.cancel_task()
        self.assertEqual(client.task_details.status, TaskStatus.DONE)

    def test_cancel_task_goal_terminated_before_cancel(self) -> None:
        """Case where the goal has already been finished when trying to cancel it.

        Checks that goal_cb is ran only once per goal and we do not throw error for the cancel.
        """
        client = ActionTaskClient(self._node, self._task_details, self._task_specs, action_clients={})
        client.start_task_async(Fibonacci.Goal(order=0))
        client.goal_done.wait(timeout=1)

        # Reset state. Simulates a state where goal has finished but the response did not arrive to task manager
        handle = ClientGoalHandle(
            goal_id=client._goal_handle.goal_id,  # type: ignore[union-attr]
            action_client=client._client,
            goal_response=Fibonacci.Result,
        )
        client._goal_handle = handle
        client._result_future._done = False  # type: ignore[union-attr]
        client._result_future.add_done_callback(client._goal_done_cb)  # type: ignore[union-attr]
        client.goal_done.clear()

        client.cancel_task()
        self.assertEqual(client.task_details.status, TaskStatus.CANCELED)


if __name__ == "__main__":
    unittest.main()
