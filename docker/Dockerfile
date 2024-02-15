FROM ros:humble

# Disable terminal interaction for apt
ENV DEBIAN_FRONTEND=noninteractive

# Make default shell in Dockerfile bash instead of sh
SHELL ["/bin/bash", "-c"]

# Setup workspace and some handy env variables
ENV ROS2_WS=/workspaces/ros2_ws
RUN mkdir -p ${ROS2_WS}/src
ENV ROS_LOG_DIR=/workspaces/logs
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

# Install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ros-${ROS_DISTRO}-rmw-cyclonedds-cpp \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install task_manager_msgs
RUN cd ${ROS2_WS}/src && git clone https://github.com/Karelics/task_manager_msgs

# Build Task Manager in the newly created workspace
COPY . ${ROS2_WS}/src/task_manager
RUN cd ${ROS2_WS} && \
    apt-get update && \
    . /opt/ros/${ROS_DISTRO}/setup.bash && \
    rosdep install --from-paths src --ignore-src -r -y --rosdistro ${ROS_DISTRO} && \
    colcon build --symlink-install && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Source ROS and the new workspace automatically when new terminal is opened
RUN echo ". /opt/ros/${ROS_DISTRO}/setup.bash" >> ~/.bashrc && \
    echo ". ${ROS2_WS}/install/setup.bash" >> ~/.bashrc

# Fix autocompletion
RUN echo 'eval "$(register-python-argcomplete3 colcon)"' >> /opt/ros/${ROS_DISTRO}/setup.sh

# Source the new workspace in the main terminal
COPY docker/ros_entrypoint.sh /ros_entrypoint.sh
ENTRYPOINT ["/ros_entrypoint.sh"]

CMD ["bash"]
