FROM osrf/ros:humble-desktop-full

# Disable terminal interaction for apt
ENV DEBIAN_FRONTEND=noninteractive

# Make default shell in Dockerfile bash instead of sh
SHELL ["/bin/bash", "-c"]

# Setup cyclone DDS
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

# Install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ros-${ROS_DISTRO}-rmw-cyclonedds-cpp \
    ros-$ROS_DISTRO-navigation2 \
    ros-$ROS_DISTRO-nav2-bringup \
    ros-$ROS_DISTRO-turtlebot3-gazebo \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Fix autocompletion
RUN echo 'eval "$(register-python-argcomplete3 colcon)"' >> /opt/ros/${ROS_DISTRO}/setup.sh

# Source ROS workspace automatically when new terminal is opened
RUN echo ". /opt/ros/${ROS_DISTRO}/setup.bash" >> ~/.bashrc


# Source ROS in the main terminal
ENTRYPOINT ["/ros_entrypoint.sh"]

CMD ["bash"]
