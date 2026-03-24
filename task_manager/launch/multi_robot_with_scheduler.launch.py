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
Launch file to start multiple robot task managers and the UAV task scheduler.

This demonstrates a complete multi-robot setup with centralized task scheduling.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node, PushRosNamespace
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """Launch multiple task managers and the scheduler."""

    # Declare launch arguments
    num_robots_arg = DeclareLaunchArgument(
        "num_robots",
        default_value="2",
        description="Number of robots to launch (1-10)",
    )

    robot_params_file_arg = DeclareLaunchArgument(
        "robot_params_file",
        default_value=PathJoinSubstitution(
            [FindPackageShare("task_manager"), "params", "task_manager_defaults.yaml"]
        ),
        description="Path to the parameters file for robot task managers",
    )

    scheduler_params_file_arg = DeclareLaunchArgument(
        "scheduler_params_file",
        default_value=PathJoinSubstitution(
            [FindPackageShare("task_manager"), "params", "uav_task_scheduler_defaults.yaml"]
        ),
        description="Path to the parameters file for the UAV Task Scheduler",
    )

    # Get launch configurations
    num_robots = LaunchConfiguration("num_robots")
    robot_params = LaunchConfiguration("robot_params_file")
    scheduler_params = LaunchConfiguration("scheduler_params_file")

    # Create task manager nodes for each robot
    # Note: In a real deployment, these would run on separate machines
    # Here we simulate by using namespaces
    robot_nodes = []

    # Generate robot nodes dynamically based on num_robots
    # For simplicity in the launch file, we'll create a fixed number
    # In practice, you'd use a Python loop or launch from separate machines

    # Robot 1
    robot1 = GroupAction([
        PushRosNamespace("uav1"),
        Node(
            package="task_manager",
            executable="task_manager_node.py",
            name="task_manager",
            output="screen",
            parameters=[robot_params],
        ),
    ])

    # Robot 2
    robot2 = GroupAction([
        PushRosNamespace("uav2"),
        Node(
            package="task_manager",
            executable="task_manager_node.py",
            name="task_manager",
            output="screen",
            parameters=[robot_params],
        ),
    ])

    # Robot 3 (optional)
    robot3 = GroupAction([
        PushRosNamespace("uav3"),
        Node(
            package="task_manager",
            executable="task_manager_node.py",
            name="task_manager",
            output="screen",
            parameters=[robot_params],
        ),
    ])

    # UAV Task Scheduler Node
    scheduler_node = Node(
        package="task_manager",
        executable="mrs_task_scheduler_node.py",
        name="uav_task_scheduler",
        output="screen",
        parameters=[scheduler_params],
    )

    return LaunchDescription([
        num_robots_arg,
        robot_params_file_arg,
        scheduler_params_file_arg,
        robot1,
        robot2,
        # robot3,  # Uncomment to add a third robot
        scheduler_node,
    ])
