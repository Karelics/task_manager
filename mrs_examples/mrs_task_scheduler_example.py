#!/usr/bin/env python3

"""
Example script demonstrating how to send tasks to the UAV Task Scheduler.

The scheduler will automatically distribute tasks to available robots.
"""

import json
import asyncio

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from task_manager_msgs.action import ExecuteTask
from rosbridge_library.internal.message_conversion import extract_values
from mrs_msgs.srv import Vec4

def get_goto_req_in_json(x, y, z, h):
    """Returns Vec4.Request in json format."""

    goto_req = Vec4.Request()
    goto_req.goal[0] = x
    goto_req.goal[1] = y
    goto_req.goal[2] = z
    goto_req.goal[3] = h

    return json.dumps(extract_values(goto_req))

class SchedulerTaskClient(Node):
    """Simple client to send tasks to the UAV Task Scheduler."""

    def __init__(self):
        super().__init__("scheduler_task_client_example")
        self._action_client = ActionClient(
            self, ExecuteTask, "/uav_task_scheduler/execute_task"
        )


    def send_goto_task(self, source: str = "Example"):
        """Send a task to the scheduler."""
        self._action_client.wait_for_server()

        goal = ExecuteTask.Goal()
        goal.task_name = "goto_action"
        goal.source = source
        goal.task_data = get_goto_req_in_json(x=-29, y=-49, z=5, h=0)

        self.get_logger().info(f"Sending task: goto")
        goal_handle = self._action_client.send_goal_async(goal)

        return

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
        goal_handle = self._action_client.send_goal_async(goal_msg)

        return

        if not goal_handle.accepted:
            self.get_logger().error("Task was rejected!")
            return None

        self.get_logger().info("Task accepted by scheduler, waiting for result...")

        result_future =goal_handle.get_result_async()

        result = result_future.result
        self.get_logger().info(
            f"Task completed with status: {result.task_status}, result: {result.task_result}"
        )
        return result


def main():
    """Example usage of the scheduler."""
    rclpy.init()

    client = SchedulerTaskClient()

    client.send_goto_task(source="ExampleScript")

    # # Example 1: Send a simple wait task
    # # This should be distributed to one of the available robots
    # client.send_task(
    #     task_name="system/wait",
    #     task_data={"duration": 10.0},
    #     source="ExampleScript",
    # )
    #
    # # Example 2: Send another task
    # # This should be distributed to a different robot if available
    # client.send_task(
    #     task_name="system/wait",
    #     task_data={"duration": 5.0},
    #     source="ExampleScript",
    # )

    client.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
