import json
from time import sleep

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from task_manager_msgs.action import ExecuteTask
from nav2_msgs.action import NavigateToPose, Spin

from rosbridge_library.internal.message_conversion import extract_values, populate_instance


def start_navigation_task(execute_task_client):
    nav2_goal = NavigateToPose.Goal()
    nav2_goal.pose.header.frame_id = "map"
    nav2_goal.pose.pose.position.x = 0.53
    nav2_goal.pose.pose.position.y = -0.58

    goal = ExecuteTask.Goal()
    goal.task = "navigation/navigate_to_pose"
    goal.source = "Nav2_example"
    goal.task_data = json.dumps(extract_values(nav2_goal))

    print("Starting navigate to pose task.")
    execute_task_client.send_goal_async(goal)


def start_spin_task(execute_task_client):
    """ Spin for 360 degrees"""
    spin_goal = Spin.Goal(target_yaw=6.283)

    goal = ExecuteTask.Goal()
    goal.task = "navigation/spin"
    goal.source = "Nav2_example"
    goal.task_data = json.dumps(extract_values(spin_goal))

    print("Starting spin task.")
    execute_task_client.send_goal_async(goal)


if __name__ == "__main__":
    rclpy.init()

    node = Node("nav2_task_manager_example")
    client = ActionClient(node, ExecuteTask, "/task_manager/execute_task")
    client.wait_for_server()
    start_navigation_task(client)
    sleep(5)
    start_spin_task(client)

    # node.spin_once()

    # try:
    #     rclpy.spin(node)
    # except KeyboardInterrupt:
    #     pass

    node.destroy_node()
    rclpy.try_shutdown()
