echo "arming"

ros2 service call /$UAV_NAME/hw_api/arming std_srvs/srv/SetBool '{"data": true}' 

sleep 1.0

echo "midair activation"

ros2 service call /$UAV_NAME/uav_manager/midair_activation std_srvs/srv/Trigger '{}'
