#  ------------------------------------------------------------------
#   Copyright (C) Karelics Oy - All Rights Reserved
#   Unauthorized copying of this file, via any medium is strictly
#   prohibited. All information contained herein is, and remains
#   the property of Karelics Oy.
#  ------------------------------------------------------------------
import os.path

# ROS
from ament_index_python import get_package_share_path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    """Launches Task Manager."""
    params = LaunchConfiguration("params_file")

    task_manager = Node(
        name="task_manager",
        package="task_manager",
        executable="task_manager_node.py",
        emulate_tty=True,
        output={"both": {"screen", "log", "own_log"}},
        parameters=[params],
        arguments=["--ros-args", "--log-level", "info"],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "params_file",
                description="Full path to the ROS2 parameters file to use for Task Manager node",
                default_value=os.path.join(
                    get_package_share_path("task_manager"), "params", "task_manager_example.yaml"
                ),
            ),
            task_manager,
        ]
    )
