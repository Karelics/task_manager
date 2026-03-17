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

"""
Example script demonstrating how to send tasks to the UAV Task Scheduler.

The scheduler will automatically distribute tasks to available robots.
"""

import json

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from task_manager_msgs.action import ExecuteTask


class SchedulerTaskClient(Node):
    """Simple client to send tasks to the UAV Task Scheduler."""

    def __init__(self):
        super().__init__("scheduler_task_client_example")
        self._action_client = ActionClient(
            self, ExecuteTask, "/uav_task_scheduler/execute_task"
        )

    def send_task(self, task_name: str, task_data: dict, source: str = "Example"):
        """Send a task to the scheduler."""
        self.get_logger().info(f"Waiting for scheduler action server...")
        self._action_client.wait_for_server()

        goal_msg = ExecuteTask.Goal()
        goal_msg.task_id = ""  # Let scheduler generate ID
        goal_msg.task_name = task_name
        goal_msg.source = source
        goal_msg.task_data = json.dumps(task_data)

        self.get_logger().info(f"Sending task: {task_name}")
        send_goal_future = self._action_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self, send_goal_future)

        goal_handle = send_goal_future.result()
        if not goal_handle.accepted:
            self.get_logger().error("Task was rejected!")
            return None

        self.get_logger().info("Task accepted by scheduler, waiting for result...")
        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future)

        result = result_future.result().result
        self.get_logger().info(
            f"Task completed with status: {result.task_status}, result: {result.task_result}"
        )
        return result


def main():
    """Example usage of the scheduler."""
    rclpy.init()

    client = SchedulerTaskClient()

    # Example 1: Send a simple wait task
    # This should be distributed to one of the available robots
    client.send_task(
        task_name="system/wait",
        task_data={"duration": 3.0},
        source="ExampleScript",
    )

    # Example 2: Send another task
    # This should be distributed to a different robot if available
    client.send_task(
        task_name="system/wait",
        task_data={"duration": 2.0},
        source="ExampleScript",
    )

    # Example 3: Send a navigation task (if configured in robot's task_manager)
    # Uncomment if your robots have navigation configured
    # client.send_task(
    #     task_name="navigation/navigate_to_pose",
    #     task_data={
    #         "pose": {
    #             "header": {"frame_id": "map"},
    #             "pose": {
    #                 "position": {"x": 1.0, "y": 2.0, "z": 0.0},
    #                 "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
    #             }
    #         }
    #     },
    #     source="ExampleScript"
    # )

    client.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
