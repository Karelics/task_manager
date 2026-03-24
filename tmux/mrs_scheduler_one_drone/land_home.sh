echo "landing"

ros2 service call /uav1/uav_manager/land_home std_srvs/srv/Trigger '{}' 
