#!/usr/bin/env python3

import os.path
from ament_index_python import get_package_share_path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch.substitutions import PythonExpression, IfElseSubstitution, PathJoinSubstitution, EnvironmentVariable, LaunchConfiguration


def generate_launch_description() -> LaunchDescription:
    """Launches Task Manager."""
    #params = LaunchConfiguration("params_file")

    # #{ custom_config

    ld = LaunchDescription()

    uav_name = LaunchConfiguration('uav_name')

    ld.add_action(DeclareLaunchArgument(
        'uav_name',
        default_value=os.getenv('UAV_NAME', "uav1"),
        description="The uav name used for namespacing.",
    ))

    custom_config = LaunchConfiguration('custom_config')

    # this adds the args to the list of args available for this launch files
    # these args can be listed at runtime using -s flag
    # default_value is required to if the arg is supposed to be optional at launch time
    ld.add_action(DeclareLaunchArgument(
        'custom_config',
        default_value=os.path.join(get_package_share_path("task_manager"), "params", "task_manager_defaults.yaml"),
        description="Path to the custom configuration file. The path can be absolute, starting with '/' or relative to the current working directory",
    ))

    # behaviour:
    #     custom_config == "" => custom_config: ""
    #     custom_config == "/<path>" => custom_config: "/<path>"
    #     custom_config == "<path>" => custom_config: "$(pwd)/<path>"
    custom_config = IfElseSubstitution(
            condition=PythonExpression(['"', custom_config, '" != "" and ', 'not "', custom_config, '".startswith("/")']),
            if_value=PathJoinSubstitution([EnvironmentVariable('PWD'), custom_config]),
            else_value=custom_config
    )

    # #} end of custom_config

    task_manager_node = Node(
        name="task_manager",
        package="task_manager",
        namespace=uav_name,
        executable="task_manager_node.py",
        emulate_tty=True,
        output={"both": {"screen", "log", "own_log"}},
        parameters=[custom_config],
        arguments=["--ros-args", "--log-level", "info"],
    )

    ld.add_action(task_manager_node)

    return ld
