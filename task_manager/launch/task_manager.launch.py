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
                    get_package_share_path("task_manager"), "params", "task_manager_defaults.yaml"
                ),
            ),
            task_manager,
        ]
    )
