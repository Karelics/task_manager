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


# ROS
from rclpy.node import Node

# Karelics messages
from task_manager_msgs.srv import CancelTasks, StopTasks

# Current package
from task_manager.active_tasks import ActiveTasks
from task_manager.task_client import CancelTaskFailedError
from task_manager.task_specs import TaskServerType, TaskSpecs


class StopTasksService:
    """Implements Stop-command."""

    def __init__(self, node: Node, active_tasks: ActiveTasks):
        self.node = node
        self.active_tasks = active_tasks

    def service_cb(self, _request: StopTasks.Request, response: StopTasks.Response) -> StopTasks.Response:
        """Stops all the currently active tasks that have 'cancel_on_stop' field set to True."""
        try:
            self.active_tasks.cancel_tasks_on_stop()
            response.success = True
        except CancelTaskFailedError as e:
            self.node.get_logger().error(f"Failed to stop some tasks on STOP command: {e}")
            response.success = False

        return response

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        """Returns TaskSpecs object that describes the task properties."""
        return TaskSpecs(
            task_name="system/stop",
            blocking=False,
            cancel_on_stop=False,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=StopTasks,
            task_server_type=TaskServerType.SERVICE,
            service_success_field="success",
        )


class CancelTasksService:
    """Cancel any task based on the task_id."""

    def __init__(self, node: Node, active_tasks: ActiveTasks):
        self.node = node
        self.active_tasks = active_tasks

    def service_cb(self, request: CancelTasks.Request, response: CancelTasks.Response) -> CancelTasks.Response:
        """Cancels the currently active tasks by given task_id."""
        cancelled = []
        response.success = True
        for task_id in request.cancelled_tasks:
            try:
                self.active_tasks.cancel_task(task_id)
            except KeyError:
                self.node.get_logger().warning(
                    f"Tried to cancel a task with ID {task_id}, but the task is not active. "
                    f"Considering as a successful cancel."
                )
            except CancelTaskFailedError as e:
                self.node.get_logger().error(f"Failed to cancel task with ID {task_id}: {e}")
                response.success = False
                continue

            cancelled.append(task_id)

        response.successful_cancels = cancelled
        return response

    @staticmethod
    def get_task_specs(topic: str) -> TaskSpecs:
        """Returns TaskSpecs object that describes the task properties."""
        return TaskSpecs(
            task_name="system/cancel_task",
            blocking=False,
            cancel_on_stop=False,
            topic=topic,
            cancel_reported_as_success=False,
            reentrant=False,
            msg_interface=CancelTasks,
            task_server_type=TaskServerType.SERVICE,
            service_success_field="success",
        )
