#!/usr/bin/env python3

#  ------------------------------------------------------------------
#   Copyright (C) Karelics Oy - All Rights Reserved
#   Unauthorized copying of this file, via any medium is strictly
#   prohibited. All information contained herein is, and remains
#   the property of Karelics Oy.
#  ------------------------------------------------------------------

import json
from typing import Any, Callable, Dict, Optional

# ROS
from rclpy.action import ActionClient
from rclpy.client import Client
from rclpy.node import Node

# Thirdparty
from rosbridge_library.internal.message_conversion import FieldTypeMismatchException, populate_instance

# Karelics messages
from task_manager_msgs.action import ExecuteTask
from task_manager_msgs.msg import TaskStatus

# Current package
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import (
    ActionTaskClient,
    CancelTaskFailedError,
    ServiceTaskClient,
    TaskClient,
    TaskStartError,
)
from task_manager.task_details import TaskDetails
from task_manager.task_specs import TaskServerType, TaskSpecs

# pylint: disable=too-few-public-methods
# Fine in this case


class TaskRegistrator:
    """Handles the logic of starting new tasks."""

    # Prefix for each task topic
    TASK_TOPIC_PREFIX: str = "/task_manager/task"

    def __init__(
        self,
        node: Node,
        active_tasks: ActiveTasks,
        task_done_cb: Optional[Callable[["TaskSpecs", "TaskDetails"], None]] = None,
    ):
        """
        :param node: parent ROS node
        :param active_tasks: Active tasks list that contains currently active tasks
        :param task_done_cb: callback to be called when a task finishes
        """
        self._node = node
        self._active_tasks = active_tasks
        self._task_done_cb = task_done_cb

        # Action and service clients for each task, which can be reused for TaskClient. At the time of writing,
        # rclpy's .destroy() isn't safe for action clients and servers, which is why we want to
        # reuse the clients
        self._action_clients: Dict[str, ActionClient] = {}  # [task_name, ActionClient]
        self._service_clients: Dict[str, Client] = {}  # [task_name, Client]

        self.cancel_task_timeout = 5

    def start_new_task(self, request: ExecuteTask.Goal, task_specs: TaskSpecs) -> TaskClient:
        """Starts a task and cancels any conflicting tasks.

        :raises DuplicateTaskIdException: If task with the same ID already exists
        :raises ROSGoalParsingError: If task data parsing fails
        :raises TaskStartError: If starting of the task fails
        """
        if self._duplicate_id_exists(request.task_id):
            raise DuplicateTaskIdException(f"Task with same the task ID {request.task_id} is already running")

        if not task_specs.reentrant:
            self._cancel_task_of_same_type(request.task)

        if task_specs.blocking:
            self._cancel_active_blocking_task()

        # TaskDetails are needed for now to support ActionTaskClients. It duplicates some info in TaskSpecs.
        task_details = TaskDetails(
            task_id=request.task_id,
            source=request.source,
            status=TaskStatus.RECEIVED,
        )

        if task_specs.task_server_type == TaskServerType.ACTION:
            task_client: TaskClient = ActionTaskClient(
                node=self._node,
                task_specs=task_specs,
                task_details=task_details,
                action_clients=self._action_clients,
                cancel_task_timeout=self.cancel_task_timeout,
            )
            msg_interface = task_specs.msg_interface.Goal()
        else:
            task_client: TaskClient = ServiceTaskClient(
                node=self._node,
                task_specs=task_specs,
                task_details=task_details,
                service_clients=self._service_clients,
                cancel_task_timeout=self.cancel_task_timeout,
            )
            msg_interface = task_specs.msg_interface.Request()

        task_client.register_done_callback(self._task_done_cb)

        task_goal_message = populate_msg(task_data=request.task_data, msg_interface=msg_interface)
        task_client.start_task_async(task_goal_message)

        self._active_tasks.add(task_client)
        return task_client

    def _duplicate_id_exists(self, task_id: str) -> bool:
        """Checks if task already exists with a given ID.

        :param task_id: Unique Task ID
        :return: True if a duplicate task was found, otherwise false.
        """
        for task_cli in self._active_tasks.get_active_tasks():
            if task_cli.task_details.task_id == task_id:
                return True
        return False

    def _cancel_task_of_same_type(self, task_name: str) -> None:
        """Cancels the task with the same name.

        :raises TaskStartError: If fails to cancel the previous task
        """
        active_task_clients = self._active_tasks.get_active_tasks_by_name(task_name)
        if not active_task_clients:
            return

        if len(active_task_clients) > 1:
            self._node.get_logger().error("Found multiple task clients with the same name!")

        for task_client in active_task_clients:
            try:
                task_client.cancel_task()
            except CancelTaskFailedError as e:
                raise TaskStartError("Failed to cancel previous task of same type.") from e

    def _cancel_active_blocking_task(self) -> None:
        """Cancels the active blocking task if one is running.

        :raises TaskStartError: if task cancelling fails.
        """
        task_client = self._active_tasks.get_blocking_task()

        if task_client is None:
            return

        try:
            task_client.cancel_task()
        except CancelTaskFailedError as e:
            raise TaskStartError(
                f"Currently ongoing task {task_client.task_specs.task_name} is blocking the task start."
            ) from e


def populate_msg(task_data: str, msg_interface: Any):
    """Returns ROS message from a dictionary formatted message.

    :raises ROSGoalParsingError: If message parsing fails
    """
    try:
        task_data = json.loads(task_data)
    except ValueError as e:
        raise ROSGoalParsingError(f"Invalid JSON format: Couldn't parse task data to json: '{task_data}'") from e

    try:
        task_goal_message = populate_message_from_json(task_data, msg_interface)
    except (KeyError, RuntimeError, FieldTypeMismatchException) as e:
        fields_and_types = msg_interface.get_fields_and_field_types()
        raise ROSGoalParsingError(
            f"Unable to parse task data, check the message interface for the correct message data format. "
            f"{msg_interface} requires the following fields and types: {fields_and_types}. "
            f"The Following error was received: {str(e)}"
        ) from e
    return task_goal_message


def populate_message_from_json(data: Dict[str, Any], message_instance: Any) -> Any:
    """Populates ros message with given data. Data may contain extra fields which are simply ignored. Has same interface
    as populate_instance from rosbridge_library.

    :param data: a dictionary of the input data
    :param message_instance: Instance of the wanted message type
    :return Message Instance with fields filled with input data

    :raises rosbridge_library.internal.message_conversion.FieldTypeMismatchException: if types in the message fields
    do not match.
    :raises KeyError: if input data does not contain the keys of the message type
    :raises RuntimeError: if input data does not contain any of the keys of the message type
    """
    filtered_data = {}
    message_fields = set(message_instance.get_fields_and_field_types().keys())

    if not message_fields:
        # Message has no fields so nothing to update.
        return message_instance

    data_keys = set(data.keys())
    common_keys = message_fields & data_keys

    if not common_keys:
        raise RuntimeError(
            "Unable to parse task data, "
            "there should be at least one common key, "
            "most likely a different goal type was expected. "
            f"Got type: {type(message_instance)} with fields {message_fields}"
        )

    # Only update the keys that are in the message
    for key in common_keys:
        filtered_data[key] = data[key]

    return populate_instance(filtered_data, message_instance)


class DuplicateTaskIdException(Exception):
    """Task ID already exists."""


class ROSGoalParsingError(Exception):
    """Parsing of the ROS Goal message fails."""
