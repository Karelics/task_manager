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
import threading
import time
import unittest
import uuid
from asyncio import Future
from typing import List, Optional

# ROS
import rclpy
from rclpy import Parameter
from rclpy.action.client import ActionClient, ClientGoalHandle
from rclpy.action.server import CancelResponse
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

# Thirdparty
from mock_servers import create_add_two_ints_service, create_fib_action_server
from rosbridge_library.internal.message_conversion import extract_values

# ROS messages
from example_interfaces.action import Fibonacci
from example_interfaces.srv import AddTwoInts

# Karelics messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import ActiveTaskArray
from task_manager_msgs.srv import CancelTasks, StopTasks

# Current package
from task_manager.active_tasks import ActiveTasks
from task_manager.task_manager_node import TaskManager
from task_manager.task_registrator import TaskRegistrator

# pylint: disable=too-few-public-methods  # helper class for tests
# pylint: disable=protected-access

TASK_TOPIC_PREFIX = "/test/task"


class TaskManagerTestNode(unittest.TestCase):
    """Sets up a spinning node and test tasks for the tests that require them."""

    def setUp(self) -> None:
        rclpy.init()
        # Before starting task nodes, override the default ROS topic to not interfere with the normal ones
        TaskRegistrator.TASK_TOPIC_PREFIX = TASK_TOPIC_PREFIX

        params = TaskManagerNodeParams()
        params.add_fibonacci_task(task_name="fibonacci")
        params.add_fibonacci_task(task_name="fibonacci_2")
        params.add_fibonacci_task(task_name="fibonacci_blocking", blocking=True)
        params.add_fibonacci_task(task_name="fibonacci_blocking_2", blocking=True)
        params.add_fibonacci_task(task_name="fibonacci_cancel_on_stop", cancel_on_stop=True)
        params.add_fibonacci_task(task_name="fibonacci_non_cancelable", cancel_on_stop=True)
        params.add_service_task_add_two_ints(task_name="add_two_ints", blocking=True)

        self.task_manager_node: TaskManager = TaskManager(
            active_tasks=ActiveTasks(), parameter_overrides=params.get_params()
        )
        self.task_manager_node.declare_tasks()
        self.task_manager_node.setup_system_tasks()

        self.test_tasks_node = TestTasksNode()
        self.executor = MultiThreadedExecutor()

        self.executor.add_node(self.task_manager_node)
        self.executor.add_node(self.test_tasks_node)
        self.spin_thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.spin_thread.start()

        self.execute_task_client = ActionClient(self.task_manager_node, ExecuteTask, "/task_manager/execute_task")
        self.execute_task_client.wait_for_server(1)

        # Track the active tasks to track which tasks have started their execution
        self.task_manager_node.create_subscription(
            ActiveTaskArray, "/task_manager/active_tasks", self._active_tasks_cb, qos_profile=10
        )
        self._tasks_started = []

    def _active_tasks_cb(self, active_tasks_msg):
        for active_task in active_tasks_msg.active_tasks:
            if active_task.task_id not in self._tasks_started:
                self._tasks_started.append(active_task.task_id)

    def tearDown(self) -> None:
        self.task_manager_node.destroy_node()
        self.test_tasks_node.destroy_node()
        rclpy.try_shutdown()
        self.spin_thread.join()

    @staticmethod
    def _get_response(future, timeout=5) -> ClientGoalHandle:
        start_time = time.time()
        while not future.done() and time.time() <= start_time + timeout:
            time.sleep(0.01)
        return future.result()

    def wait_for_task_start(self, task_id: str, timeout: float = 5.0):
        """Utility function to wait for the task to be started."""
        start = time.time()
        while task_id not in self._tasks_started:
            assert time.time() - start < timeout
            time.sleep(0.01)

    def start_fibonacci_action_task(
        self, task_name: str = "fibonacci", run_time_secs: int = 3, task_id: Optional[str] = None
    ) -> ClientGoalHandle:
        """Creates the goal and starts Fibonacci task.

        :param task_name: name of the task, will be prefixed with TASK_TOPIC_PREFIX to get the action topic
        :param task_id: Unique ID for the task. Autogenerated if empty.
        :param run_time_secs: Run time in seconds. 0 completes the task as fast as possible
        """
        task_goal = create_fibonacci_task_goal(task_name=task_name, run_time_secs=run_time_secs, task_id=task_id)
        return self._start_task(task_goal)

    def start_add_two_ints_service_task(
        self,
        task_id: Optional[str] = None,
        task_name: str = "add_two_ints",
        run_time_secs: int = 0,
        request: Optional[AddTwoInts.Request] = None,
    ) -> ClientGoalHandle:
        """Creates the goal and starts the Add Two Ints Service task."""
        goal = create_add_two_ints_task_goal(task_id, task_name, run_time_secs, request)
        return self._start_task(goal)

    def _start_task(self, goal):
        future: Future = self.execute_task_client.send_goal_async(goal)
        goal_handle = self._get_response(future)
        return goal_handle

    def execute_stop_task(self):
        """Calls system/stop."""
        task_data = json.dumps(extract_values(StopTasks.Request()))
        goal = ExecuteTask.Goal(task="system/stop", task_data=task_data, source="")
        goal_handle = self._start_task(goal=goal)
        return goal_handle.get_result()

    def execute_cancel_task(self, task_ids: List[str]):
        """Calls system/cancel_task with given task IDs."""
        cancel_goal = CancelTasks.Request()
        cancel_goal.cancelled_tasks = task_ids
        task_data = json.dumps(extract_values(cancel_goal))
        goal = ExecuteTask.Goal(task="system/cancel_task", task_data=task_data, source="")
        goal_handle = self._start_task(goal=goal)
        return goal_handle.get_result()


class TestTasksNode(Node):
    """Launches test action servers, each with different behavior or task configuration."""

    def __init__(self) -> None:
        super().__init__("tm_test_tasks_node")
        self.fib_server = create_fib_action_server(node=self, action_name="fibonacci")
        self.fib_server_2 = create_fib_action_server(node=self, action_name="fibonacci_2")
        self.fib_blocking_server = create_fib_action_server(node=self, action_name="fibonacci_blocking")
        self.fib_blocking_server_2 = create_fib_action_server(node=self, action_name="fibonacci_blocking_2")
        self.fib_cancel_on_stop_server = create_fib_action_server(node=self, action_name="fibonacci_cancel_on_stop")
        self.fib_non_cancelable = create_fib_action_server(node=self, action_name="fibonacci_non_cancelable")
        self.add_two_ints = create_add_two_ints_service(node=self, service_name="add_two_ints")

        self.fib_non_cancelable.register_cancel_callback(self._cancel_cb)

    @staticmethod
    def _cancel_cb(_goal_handle):
        return CancelResponse.REJECT


class TaskManagerNodeParams:
    """Utility class to easier declare Task Manager parameters."""

    def __init__(self):
        self._params = []
        self._tasks = []

    def add_task(self, task_name, topic, msg_interface: str, *, blocking=False, cancel_on_stop=False):
        """Adds a new task to parameter list."""
        self._params.extend(
            [
                Parameter(name=f"{task_name}.task_name", value=task_name),
                Parameter(name=f"{task_name}.topic", value=topic),
                Parameter(name=f"{task_name}.msg_interface", value=msg_interface),
                Parameter(name=f"{task_name}.blocking", value=blocking),
                Parameter(name=f"{task_name}.cancel_on_stop", value=cancel_on_stop),
            ]
        )
        self._tasks.append(task_name)

    def add_fibonacci_task(self, task_name, blocking=False, cancel_on_stop=False):
        """Adds a new fibonacci task to parameter list."""
        self.add_task(
            task_name=task_name,
            topic=f"/{task_name}",
            msg_interface="example_interfaces.action.Fibonacci",
            blocking=blocking,
            cancel_on_stop=cancel_on_stop,
        )

    def add_service_task_add_two_ints(self, task_name, blocking=False):
        """Adds a new 'add two ints' service task to parameter list."""
        self.add_task(
            task_name=task_name,
            topic=f"/{task_name}",
            msg_interface="example_interfaces.srv.AddTwoInts",
            blocking=blocking,
            cancel_on_stop=False,
        )

    def get_params(self):
        """Returns the Task Manager parameters."""
        self._params.append(Parameter(name="tasks", value=self._tasks))
        return self._params


def create_fibonacci_task_goal(
    task_id: Optional[str] = None,
    task_name: str = "fibonacci",
    goal: Optional[Fibonacci.Goal] = None,
    run_time_secs: int = 0,
) -> ExecuteTask.Goal:
    """Populates ExecuteTask goal for Fibonacci task based on given arguments."""
    if task_id is None:
        task_id = str(uuid.uuid4())

    if goal is None:
        goal = Fibonacci.Goal()
        goal.order = run_time_secs

    goal_dict = json.dumps(extract_values(goal))

    return ExecuteTask.Goal(task_id=task_id, task=task_name, task_data=goal_dict, source="CLOUD")


def create_add_two_ints_task_goal(
    task_id: Optional[str] = None,
    task_name: str = "add_two_ints",
    run_time_secs: int = 0,
    request: Optional[AddTwoInts.Request] = None,
):
    """Populates AddTwoInts request based on given arguments."""
    if task_id is None:
        task_id = str(uuid.uuid4())

    if request is None:
        request = AddTwoInts.Request(a=0, b=run_time_secs)

    add_two_ints_goal = json.dumps(extract_values(request))

    return ExecuteTask.Goal(task_id=task_id, task=task_name, task_data=add_two_ints_goal, source="CLOUD")
