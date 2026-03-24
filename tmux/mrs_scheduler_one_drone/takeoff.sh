echo "arming"

ros2 service call /uav1/hw_api/arming std_srvs/srv/SetBool '{"data": true}' 

sleep 1.0

echo "toggling offboard"

ros2 service call /uav1/hw_api/offboard std_srvs/srv/Trigger '{}' 
