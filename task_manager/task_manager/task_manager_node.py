#!/usr/bin/env python3

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
import sys
import time
import uuid
from importlib import import_module
from threading import Lock
from typing import Any, Dict, Tuple

# ROS
import rclpy
from rclpy import Parameter
from rclpy.action import ActionServer
from rclpy.action.server import CancelResponse, ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

# Thirdparty
from rosbridge_library.internal.message_conversion import extract_values

# Task Manager messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import ActiveTask, ActiveTaskArray, TaskDoneResult, TaskStatus

# Task Manager
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import CancelTaskFailedError, TaskClient, TaskStartError
from task_manager.task_details import TaskDetails
from task_manager.task_registrator import DuplicateTaskIdException, ROSGoalParsingError, TaskRegistrator
from task_manager.task_specs import TaskServerType, TaskSpecs
from task_manager.tasks.mission import Mission
from task_manager.tasks.system_tasks import CancelTasksService, StopTasksService, WaitTask
from task_manager.tasks.task_action_server import TaskActionServer
from task_manager.tasks.task_service_server import TaskServiceServer


class TaskManager(Node):
    """Node that handles Task Management by setting up TaskRegistrator, system tasks and an active tasks list."""

    def __init__(self, active_tasks: ActiveTasks, parameter_overrides=None) -> None:
        super().__init__("task_manager", parameter_overrides=parameter_overrides)

        qos = QoSProfile(
            depth=10, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL
        )
        self._active_tasks_pub = self.create_publisher(ActiveTaskArray, "/task_manager/active_tasks", qos_profile=qos)
        self.active_tasks = active_tasks
        self.active_tasks.set_active_tasks_changed_cb(self._active_tasks_changed_cb)

        self.task_registrator = TaskRegistrator(self, self.active_tasks, task_done_cb=self._task_done_cb)

        results_qos = QoSProfile(depth=10, reliability=QoSReliabilityPolicy.RELIABLE)
        self.results_pub = self.create_publisher(TaskDoneResult, "/task_manager/results", qos_profile=results_qos)

        self._enable_task_servers = self.declare_parameter("enable_task_servers", False).value

        # Lock for starting one task at a time
        self.mutex = Lock()

        self._action_server = ActionServer(
            node=self,
            action_type=ExecuteTask,
            action_name="/task_manager/execute_task",
            execute_callback=self._execute_task_action_cb,
            cancel_callback=self._cancel_cb,
            callback_group=ReentrantCallbackGroup(),
        )

        self.known_tasks: Dict[str, TaskSpecs] = {}

    def declare_tasks(self):
        """Populates the known tasks based on the ROS parameter configuration."""
        tasks = self.declare_parameter("tasks", Parameter.Type.STRING_ARRAY).value
        if not tasks:
            self.get_logger().error("No tasks were declared! Please declare them in the parameters file.")
            return

        for task in tasks:
            task_name = self.declare_parameter(f"{task}.task_name", Parameter.Type.STRING).value
            msg_interface_str = self.declare_parameter(f"{task}.msg_interface", Parameter.Type.STRING).value
            msg_interface = get_plugin_class_from_string(msg_interface_str)
            service_success_field = self.declare_parameter(f"{task}.service_success_field", "").value

            # Check that the service_success_field truly exists in the service response, if it is set
            if service_success_field != "":
                try:
                    getattr(msg_interface.Response(), service_success_field)
                except AttributeError:
                    self.get_logger().error(
                        f"Failed to get attribute '{service_success_field}' for the task "
                        f"{task_name}. The field does not exist in the "
                        f"{msg_interface.__name__}.Response() service message. Check the task configuration. "
                    )
                    sys.exit()

            task_specs = TaskSpecs(
                task_name=task_name,
                blocking=self.declare_parameter(f"{task}.blocking", Parameter.Type.BOOL).value,
                cancel_on_stop=self.declare_parameter(f"{task}.cancel_on_stop", Parameter.Type.BOOL).value,
                topic=self.declare_parameter(f"{task}.topic", Parameter.Type.STRING).value,
                cancel_reported_as_success=self.declare_parameter(f"{task}.cancel_reported_as_success", False).value,
                reentrant=self.declare_parameter(f"{task}.reentrant", False).value,
                msg_interface=msg_interface,
                task_server_type=detect_task_server_type(msg_interface),
                service_success_field=service_success_field,
                cancel_timeout=self.declare_parameter(f"{task}.cancel_timeout", 5.0).value,
            )
            self.known_tasks[task_specs.task_name] = task_specs

            if self._enable_task_servers:
                if task_specs.task_server_type == TaskServerType.ACTION:
                    # Create an action server for the task that can be easily called from the command line,
                    # in addition to the "execute_task" action server
                    TaskActionServer(
                        node=self,
                        task_specs=task_specs,
                        task_topic_prefix=self.task_registrator.TASK_TOPIC_PREFIX,
                        execute_task_cb=self.execute_task,
                    )

                elif task_specs.task_server_type == TaskServerType.SERVICE:
                    # TODO What if we have forward slash in the task name?
                    TaskServiceServer(
                        node=self,
                        task_specs=task_specs,
                        task_topic_prefix=self.task_registrator.TASK_TOPIC_PREFIX,
                        execute_task_cb=self.execute_task,
                    )

    def setup_system_tasks(self):
        """Create servers for system tasks."""
        stop_topic = f"{self.task_registrator.TASK_TOPIC_PREFIX}/system/stop"
        cancel_topic = f"{self.task_registrator.TASK_TOPIC_PREFIX}/system/cancel_task"
        mission_topic = f"{self.task_registrator.TASK_TOPIC_PREFIX}/system/mission"
        wait_topic = f"{self.task_registrator.TASK_TOPIC_PREFIX}/system/wait"

        if not self._enable_task_servers:
            # Make services hidden. Actions cannot be hidden in a same way as services are,
            # so Missions are always public.
            stop_topic = "_" + stop_topic
            cancel_topic = "_" + cancel_topic

        stop_service = StopTasksService(self, topic=stop_topic, active_tasks=self.active_tasks)
        cancel_service = CancelTasksService(self, topic=cancel_topic, active_tasks=self.active_tasks)
        mission = Mission(self, action_name=mission_topic, execute_task_cb=self.execute_task)
        wait = WaitTask(self, topic=wait_topic)

        self.known_tasks["system/stop"] = stop_service.get_task_specs(stop_topic)
        self.known_tasks["system/cancel_task"] = cancel_service.get_task_specs(cancel_topic)
        self.known_tasks["system/mission"] = mission.get_task_specs(mission_topic)
        self.known_tasks["system/wait"] = wait.get_task_specs(wait_topic)

    def _execute_task_action_cb(self, goal_handle: ServerGoalHandle):
        request = goal_handle.request
        response = self.execute_task(request, goal_handle)

        if response.task_status == TaskStatus.DONE:
            goal_handle.succeed()
        elif response.task_status == TaskStatus.CANCELED and goal_handle.is_cancel_requested:
            # Need to also check if the cancel was requested. If the goal was cancelled
            # through a system task, we cannot set the status to be cancelled and must abort instead.
            goal_handle.canceled()
        else:  # Could be ERROR or IN_PROGRESS if the goal couldn't be cancelled
            goal_handle.abort()

        return response

    def execute_task(self, request: ExecuteTask.Goal, goal_handle: ServerGoalHandle = None) -> ExecuteTask.Result:
        """Execute a single task."""
        if request.task_id == "":
            request.task_id = str(uuid.uuid4())

        response = ExecuteTask.Result()
        response.task_id = request.task_id

        # Mutex lock required, since we need to be sure that the previous blocking task has
        # truly finished before we try to start another one from another thread.
        with self.mutex:
            task_client, error_code = self._start_task(request)

        if error_code:
            response.task_status = TaskStatus.ERROR
            response.error_code = error_code
            response.task_result = json.dumps({})

            # Normally the done result is published automatically when task_client has finished. Now we are not
            # creating the task_client at all, since the task has failed while trying to start it.
            self.results_pub.publish(
                TaskDoneResult(
                    task_id=request.task_id,
                    task_name=request.task_name,
                    task_status=response.task_status,
                    error_code=response.error_code,
                    source=request.source,
                    task_result=response.task_result,
                )
            )
            return response

        try:
            response.task_status, response.task_result = self._wait_for_task_finish(task_client, goal_handle)
        except CancelTaskFailedError as e:
            self.get_logger().error(f"Failed to cancel a task {request.task_name}: {repr(e)}")
            response.task_status = TaskStatus.IN_PROGRESS
            response.error_code = response.ERROR_TASK_CANCEL_FAILED

        return response

    @staticmethod
    def _cancel_cb(_goal_handle):
        return CancelResponse.ACCEPT

    def _start_task(self, request):
        task_client = None
        error_code = None

        if request.task_name not in self.known_tasks:
            self.get_logger().error(
                f"Unknown task: '{request.task_name}'. All the tasks needs to be declared using parameters"
            )
            return None, ExecuteTask.Result().ERROR_UNKNOWN_TASK

        self.get_logger().info(f"Got a request from '{request.source}' to start '{request.task_name}' task.")
        try:
            task_client = self.task_registrator.start_new_task(request, self.known_tasks[request.task_name])
        except DuplicateTaskIdException as error_msg:
            self.get_logger().error(repr(error_msg))
            error_code = ExecuteTask.Result().ERROR_DUPLICATE_TASK_ID
        except ROSGoalParsingError as error_msg:
            self.get_logger().error(repr(error_msg))
            error_code = ExecuteTask.Result().ERROR_TASK_DATA_PARSING_FAILED
        except TaskStartError as error_msg:
            self.get_logger().error(repr(error_msg))
            error_code = ExecuteTask.Result().ERROR_TASK_START_ERROR

        return task_client, error_code

    @staticmethod
    def _wait_for_task_finish(task_client: TaskClient, goal_handle: ServerGoalHandle = None) -> Tuple[TaskStatus, str]:
        """Waits for the running task to finish.

        :raises CancelTaskFailedError: If the task cancellation fails
        """
        while rclpy.ok() and not task_client.goal_done.is_set():
            if goal_handle and goal_handle.is_cancel_requested:
                task_client.cancel_task()
                break
            time.sleep(1 / 50)
        return task_client.task_details.status, json.dumps(extract_values(task_client.task_details.result))

    def _active_tasks_changed_cb(self, active_tasks: Dict[str, TaskClient]) -> None:
        """Publishes active tasks to local ROS topic."""
        task_messages = []
        for task_client in active_tasks.values():
            task_msg = ActiveTask()
            task_msg.task_id = task_client.task_details.task_id
            task_msg.task_name = task_client.task_specs.task_name
            task_msg.task_status = str(task_client.task_details.status)
            task_msg.source = task_client.task_details.source
            task_messages.append(task_msg)
        msg = ActiveTaskArray(active_tasks=task_messages)
        self._active_tasks_pub.publish(msg)

    def _task_done_cb(self, task_specs: TaskSpecs, task_details: TaskDetails):
        result_msg = TaskDoneResult(
            task_id=task_details.task_id,
            task_name=task_specs.task_name,
            task_status=task_details.status,
            source=task_details.source,
            task_result=json.dumps(extract_values(task_details.result)),
        )
        self.results_pub.publish(result_msg)
        self.get_logger().info(f"Task {task_specs.task_name} completed with status {task_details.status}")


def detect_task_server_type(msg_interface: Any) -> TaskServerType:
    """Automatically detects the Task Client type we need to use, based on the ROS interface.

    :raises NotImplementedError: If the message interface type is not supported
    """
    if hasattr(msg_interface, "Goal") and hasattr(msg_interface, "Result"):
        return TaskServerType.ACTION
    if hasattr(msg_interface, "Request") and hasattr(msg_interface, "Response"):
        return TaskServerType.SERVICE

    raise NotImplementedError(
        f"ROS msg interface type not recognized for type {msg_interface}. "
        f"Only action and service interfaces are supported"
    )


def get_plugin_class_from_string(string):
    """For example: 'example_interfaces.action.Fibonacci' returns Fibonacci, which is imported from
    example_interfaces.action.Fibonacci."""
    str_list = string.split(".")  # ['example_interfaces', 'action', 'Fibonacci']
    module_name = ".".join(str_list[:-1])  # example_interfaces.action
    class_name = str_list[-1]  # Fibonacci
    class_object = getattr(import_module(module_name), class_name)
    return class_object


def main() -> None:
    """Spins TaskManager Node and launches task servers for system tasks."""
    rclpy.init()

    active_tasks = ActiveTasks()
    task_manager = TaskManager(active_tasks)
    task_manager.declare_tasks()
    task_manager.setup_system_tasks()

    executor = MultiThreadedExecutor()
    try:
        rclpy.spin(task_manager, executor=executor)
    except KeyboardInterrupt:
        pass
    task_manager.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
