import json
from time import sleep

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from task_manager_msgs.action import ExecuteTask, Mission
from task_manager_msgs.msg import SubtaskGoal
from nav2_msgs.action import NavigateToPose, Spin

from rosbridge_library.internal.message_conversion import extract_values, populate_instance


def get_navigation_goal_in_json(x, y):
    """ Returns NavigateToPose.Goal in json format"""
    nav2_goal = NavigateToPose.Goal()
    nav2_goal.pose.header.frame_id = "map"
    nav2_goal.pose.pose.position.x = x
    nav2_goal.pose.pose.position.y = y
    return json.dumps(extract_values(nav2_goal))


def get_spin_goal_in_json(target_yaw_rad):
    """ Returns Spin.Goal in json format"""
    spin_goal = Spin.Goal(target_yaw=target_yaw_rad)
    return json.dumps(extract_values(spin_goal))


def start_navigation_task(execute_task_client):
    """ Executes a navigation task asynchronously """
    goal = ExecuteTask.Goal()
    goal.task = "navigation/navigate_to_pose"
    goal.source = "Nav2_example"
    goal.task_data = get_navigation_goal_in_json(x=0.53, y=-0.58)

    execute_task_client.send_goal_async(goal)


def start_spin_task(execute_task_client):
    """ Spin for 180 degrees"""
    goal = ExecuteTask.Goal()
    goal.task = "navigation/spin"
    goal.source = "Nav2_example"
    goal.task_data = get_spin_goal_in_json(3.14)

    execute_task_client.send_goal_async(goal)


def start_nav2_mission(execute_task_client):
    """ Starts a mission that navigates to 4 different poses and spins the robot in between them. """
    mission_goal = Mission.Goal(
            subtasks=[
                SubtaskGoal(task="navigation/navigate_to_pose", data=get_navigation_goal_in_json(x=0.55, y=-0.55)),
                SubtaskGoal(task="navigation/spin", data=get_spin_goal_in_json(1.57)),
                SubtaskGoal(task="navigation/navigate_to_pose", data=get_navigation_goal_in_json(x=0.55, y=0.55)),
                SubtaskGoal(task="navigation/spin", data=get_spin_goal_in_json(1.57)),
                SubtaskGoal(task="navigation/navigate_to_pose", data=get_navigation_goal_in_json(x=-0.55, y=0.55)),
                SubtaskGoal(task="navigation/spin", data=get_spin_goal_in_json(1.57)),
                SubtaskGoal(task="navigation/navigate_to_pose", data=get_navigation_goal_in_json(x=-0.55, y=-0.55)),
                SubtaskGoal(task="navigation/spin", data=get_spin_goal_in_json(1.57)),
            ]
        )

    goal = ExecuteTask.Goal()
    goal.task = "system/mission"
    goal.source = "Nav2_example"
    goal.task_data = json.dumps(extract_values(mission_goal))

    execute_task_client.send_goal_async(goal)


if __name__ == "__main__":
    rclpy.init()

    node = Node("nav2_task_manager_example")
    client = ActionClient(node, ExecuteTask, "/task_manager/execute_task")
    client.wait_for_server()

    print("Starting a blocking navigate to pose task.")
    start_navigation_task(client)
    sleep(5)
    print("Starting a blocking spin task which automatically cancels the previous blocking task.")
    start_spin_task(client)
    sleep(5)
    print("Starting nav2 mission.")
    start_nav2_mission(client)

    node.destroy_node()
    rclpy.try_shutdown()
