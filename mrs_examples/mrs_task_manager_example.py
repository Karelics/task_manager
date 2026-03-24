#!/usr/bin/env python3

import json

# ROS
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

# Thirdparty
from rosbridge_library.internal.message_conversion import extract_values

# ROS messages
from mrs_msgs.srv import Vec4
from mrs_action_msgs.action import Goto

# Task Manager messages
from task_manager_msgs.action import ExecuteTask, Mission
from task_manager_msgs.msg import SubtaskGoal


def get_goto_req_in_json(x, y, z, h):
    """Returns Vec4.Request in json format."""

    goto_req = Vec4.Request()
    goto_req.goal[0] = x
    goto_req.goal[1] = y
    goto_req.goal[2] = z
    goto_req.goal[3] = h

    return json.dumps(extract_values(goto_req))

def get_goto_action_goal_in_json(x, y, z, h):
    """Returns Vec4.Request in json format."""

    goto_req = Goto.Goal()
    goto_req.goal[0] = x
    goto_req.goal[1] = y
    goto_req.goal[2] = z
    goto_req.goal[3] = h

    return json.dumps(extract_values(goto_req))


def start_goto_task(execute_task_client):
    """Executes a navigation task asynchronously."""

    goal = ExecuteTask.Goal()
    goal.task_name = "goto"
    goal.source = "Mrs_example"
    goal.task_data = get_goto_req_in_json(x=1, y=1, z=5, h=0)

    execute_task_client.send_goal_async(goal)

def start_goto_mission(execute_task_client):
    subtasks = [
        SubtaskGoal(task_name="goto_action", task_data=get_goto_action_goal_in_json(x=0, y=10, z=5, h=0)),
        SubtaskGoal(task_name="goto_action", task_data=get_goto_action_goal_in_json(x=10, y=10, z=5, h=0)),
        SubtaskGoal(task_name="goto_action", task_data=get_goto_action_goal_in_json(x=10, y=0, z=5, h=0)),
        SubtaskGoal(task_name="goto_action", task_data=get_goto_action_goal_in_json(x=0, y=0, z=5, h=0)),
    ]

    mission_goal = Mission.Goal(subtasks=subtasks)

    goal = ExecuteTask.Goal()
    goal.task_name = "system/mission"
    goal.source = "Mrs_example"
    goal.task_data = json.dumps(extract_values(mission_goal))

    execute_task_client.send_goal_async(goal)


def start_goto_mission_2(execute_task_client):
    subtasks = [
        SubtaskGoal(task_name="goto_action", task_data=get_goto_action_goal_in_json(x=10, y=10, z=5, h=0)),
        SubtaskGoal(task_name="goto_action", task_data=get_goto_action_goal_in_json(x=20, y=20, z=5, h=0)),
        SubtaskGoal(task_name="goto_action", task_data=get_goto_action_goal_in_json(x=20, y=10, z=5, h=0)),
        SubtaskGoal(task_name="goto_action", task_data=get_goto_action_goal_in_json(x=10, y=10, z=5, h=0)),
    ]

    mission_goal = Mission.Goal(subtasks=subtasks)

    goal = ExecuteTask.Goal()
    goal.task_name = "system/mission"
    goal.source = "Mrs_example"
    goal.task_data = json.dumps(extract_values(mission_goal))

    execute_task_client.send_goal_async(goal)


if __name__ == "__main__":
    rclpy.init()

    node = Node("nav2_task_manager_example")

    client1 = ActionClient(node, ExecuteTask, "/uav1/task_manager/execute_task")
    client1.wait_for_server()
    print("Starting goto mission.")
    start_goto_mission(client1)

    client2 = ActionClient(node, ExecuteTask, "/uav2/task_manager/execute_task")
    client2.wait_for_server()
    print("Starting goto mission.")
    start_goto_mission_2(client2)

    node.destroy_node()
    rclpy.try_shutdown()
