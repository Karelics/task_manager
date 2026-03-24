#!/usr/bin/env python3

import os.path
from ament_index_python import get_package_share_path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


from launch.substitutions import PythonExpression, IfElseSubstitution, PathJoinSubstitution, EnvironmentVariable, LaunchConfiguration
def generate_launch_description():
    """Launch the UAV Task Scheduler node."""

    # Declare launch arguments
    params_file_arg = DeclareLaunchArgument(
        "params_file",
        default_value=PathJoinSubstitution(
            [FindPackageShare("task_manager"), "params", "uav_task_scheduler_defaults.yaml"]
        ),
        description="Path to the parameters file for the UAV Task Scheduler",
    )

    # UAV Task Scheduler Node
    scheduler_node = Node(
        package="task_manager",
        executable="mrs_task_scheduler_node.py",
        name="uav_task_scheduler",
        output="screen",
        parameters=[LaunchConfiguration("params_file")],
    )

    return LaunchDescription([params_file_arg, scheduler_node])
