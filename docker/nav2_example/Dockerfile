FROM osrf/ros:humble-desktop-full

# Setup user for Gazebo init mounting and GUI to work correctly
ARG UNAME=user
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} -o ${UNAME}
RUN useradd -m -u ${UID} -g ${GID} -o -s /bin/bash ${UNAME}
RUN usermod -aG sudo ${UNAME}
RUN echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
USER ${UNAME}

# Disable terminal interaction for apt
ENV DEBIAN_FRONTEND=noninteractive

# Make default shell in Dockerfile bash instead of sh
SHELL ["/bin/bash", "-c"]

# Setup cyclone DDS
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

# Install dependencies
RUN sudo apt-get update && \
    sudo apt-get install -y --no-install-recommends \
    ros-${ROS_DISTRO}-rmw-cyclonedds-cpp \
    ros-$ROS_DISTRO-navigation2 \
    ros-$ROS_DISTRO-nav2-bringup \
    ros-$ROS_DISTRO-turtlebot3-gazebo \
    && sudo apt-get clean && \
    sudo rm -rf /var/lib/apt/lists/*

# Source ROS workspace automatically when new terminal is opened
RUN sudo echo ". /opt/ros/${ROS_DISTRO}/setup.bash" >> ~/.bashrc
RUN sudo echo ". /usr/share/gazebo/setup.bash" >> ~/.bashrc


# Source ROS in the main terminal
ENTRYPOINT ["/ros_entrypoint.sh"]

CMD ["bash"]
