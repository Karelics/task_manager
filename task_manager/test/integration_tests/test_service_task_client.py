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
import time
import unittest
from unittest.mock import Mock

# ROS
import rclpy
from rclpy.executors import MultiThreadedExecutor

# Thirdparty
from mock_servers import create_add_two_ints_service

# ROS messages
from example_interfaces.srv import AddTwoInts

# Karelics messages
from task_manager_msgs.msg import TaskStatus

# Current package
from task_manager.task_client import CancelTaskFailedError, ServiceTaskClient, TaskStartError
from task_manager.task_details import TaskDetails
from task_manager.task_specs import TaskServerType, TaskSpecs

# pylint: disable=duplicate-code
# The init is very similar to test_action_task_client, but it is fine in this case


class TestServiceTaskClient(unittest.TestCase):
    """Integration tests for ServiceTaskClient."""

    def setUp(self) -> None:
        rclpy.init()

        self._node = rclpy.create_node("test_node")
        self._add_ints_srv = create_add_two_ints_service(self._node, "add_two_ints")
        self.executor = MultiThreadedExecutor()

        self.executor.add_node(self._node)
        self.spin_thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.spin_thread.start()

        self._task_specs = TaskSpecs(
            task_name="add_two_ints",
            blocking=True,
            cancel_on_stop=True,
            topic="/add_two_ints",
            cancel_reported_as_success=True,
            msg_interface=AddTwoInts,
            reentrant=True,
            task_server_type=TaskServerType.SERVICE,
        )

        self._task_details = TaskDetails(
            task_id="",
            source="",
            status=TaskStatus.RECEIVED,
        )
        self._task_clients = {}

    def tearDown(self) -> None:
        self._node.destroy_node()
        rclpy.try_shutdown()
        self.spin_thread.join()

    def test_start_task_happy_flow(self):
        """Happy flow of executing a service task."""
        task_client = ServiceTaskClient(
            self._node, task_details=self._task_details, task_specs=self._task_specs, service_clients={}
        )
        task_client.start_task_async(AddTwoInts.Request(a=0, b=0))
        self.assertEqual(task_client.task_details.status, TaskStatus.IN_PROGRESS)
        task_client.goal_done.wait(5)
        self.assertEqual(task_client.task_details.status, TaskStatus.DONE)

    def test_start_task_non_existing_service_topic(self):
        """Starting a task with a topic that doesn't exist raises an error."""
        self._task_specs.topic = "/non_existing"
        task_client = ServiceTaskClient(
            self._node, task_details=self._task_details, task_specs=self._task_specs, service_clients={}
        )
        with self.assertRaises(TaskStartError):
            task_client.start_task_async(AddTwoInts.Request(a=0, b=0))
        self.assertEqual(task_client.task_details.status, TaskStatus.ERROR)

    def test_start_task_service_not_reachable_on_second_call(self):
        """Calling a service that no longer exists will raise an error."""
        # Make first a successful call, so that the service client is stored
        service_clients = {}
        task_client = ServiceTaskClient(
            self._node, task_details=self._task_details, task_specs=self._task_specs, service_clients=service_clients
        )
        task_client.start_task_async(AddTwoInts.Request(a=0, b=0))
        task_client.goal_done.wait(5)
        self.assertEqual(task_client.task_details.status, TaskStatus.DONE)

        # Destroy the service server and try again
        self._node.destroy_service(self._add_ints_srv)
        # Sleeping here is necessary, otherwise the client's wait_for_service might still return True
        # for a brief moment. https://github.com/ros2/rclpy/issues/1191
        time.sleep(0.1)

        task_client = ServiceTaskClient(
            self._node, task_details=self._task_details, task_specs=self._task_specs, service_clients=service_clients
        )
        with self.assertRaises(TaskStartError):
            task_client.start_task_async(AddTwoInts.Request(a=0, b=0))

        # Recreate the service and check that we are again able to connect
        create_add_two_ints_service(self._node, "add_two_ints")
        task_client = ServiceTaskClient(
            self._node, task_details=self._task_details, task_specs=self._task_specs, service_clients=service_clients
        )
        task_client.start_task_async(AddTwoInts.Request(a=0, b=0))
        task_client.goal_done.wait(5)
        self.assertEqual(task_client.task_details.status, TaskStatus.DONE)

    def test_cancel(self):
        """Cancelling the service task will wait for the previous service to finish.

        If it doesn't in the given timeout, raises an error
        """
        service_clients = {}
        with self.subTest("Happy flow"):
            task_client = ServiceTaskClient(
                self._node,
                task_details=self._task_details,
                task_specs=self._task_specs,
                service_clients=service_clients,
            )
            task_client.cancel_task_timeout = 2.0
            task_client.start_task_async(AddTwoInts.Request(a=1, b=0))
            task_client.cancel_task()
            self.assertEqual(task_client.task_details.status, TaskStatus.DONE)

        with self.subTest("Service execution takes longer than our waiting timeout is"):
            task_client = ServiceTaskClient(
                self._node,
                task_details=self._task_details,
                task_specs=self._task_specs,
                service_clients=service_clients,
            )
            task_client.cancel_task_timeout = 0.5
            task_client.start_task_async(AddTwoInts.Request(a=1, b=0))
            with self.assertRaises(CancelTaskFailedError):
                task_client.cancel_task()

            self.assertTrue(task_client.goal_done.wait(1))

        with self.subTest("Task had already finished when the cancel is called"):
            logger_mock = Mock()
            self._node.get_logger = Mock(return_value=logger_mock)
            task_client = ServiceTaskClient(
                self._node,
                task_details=self._task_details,
                task_specs=self._task_specs,
                service_clients=service_clients,
            )
            task_client.cancel_task_timeout = 0.5
            task_client.start_task_async(AddTwoInts.Request(a=0, b=0))
            self.assertTrue(task_client.goal_done.wait(1))
            task_client.cancel_task()
            logger_mock.warn.assert_not_called()

    def test_callback_registration(self):
        """Check that done-callback registration and calling works as expected."""
        service_clients = {}
        done_event = threading.Event()

        def _done_callback(_task_specs, _task_details):
            done_event.set()

        with self.subTest("Happy flow: Callback is notified when the task finished"):
            task_client = ServiceTaskClient(
                self._node,
                task_details=self._task_details,
                task_specs=self._task_specs,
                service_clients=service_clients,
            )
            task_client.register_done_callback(_done_callback)
            task_client.start_task_async(AddTwoInts.Request(a=0, b=0))
            self.assertTrue(done_event.wait(1))

        done_event.clear()

        with self.subTest("Callback is notified if the callback is registered after the task has finished"):
            task_client = ServiceTaskClient(
                self._node,
                task_details=self._task_details,
                task_specs=self._task_specs,
                service_clients=service_clients,
            )
            task_client.start_task_async(AddTwoInts.Request(a=0, b=0))
            task_client.goal_done.wait(2)
            task_client.register_done_callback(_done_callback)
            self.assertTrue(done_event.wait(1))


if __name__ == "__main__":
    unittest.main()
