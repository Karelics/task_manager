#!/usr/bin/python3

import rclpy
from rclpy.node import Node
from mrs_msgs.srv import PathSrv
from mrs_msgs.msg import Reference

class Goto(Node):

    def __init__(self):

        super().__init__('goto')

        self.get_logger().info('ROS2 node initialized')

        self.get_logger().info('Setting up the client')

        self.client = self.create_client(PathSrv, "/uav1/trajectory_generation/path")

        self.timer = self.create_timer(0.1, self.doAction)

        self.get_logger().info('__init__ finished')

    def doAction(self):

        self.get_logger().info('doing the action')

        self.timer.cancel()

        while not self.client.wait_for_service(timeout_sec=3.0):
            self.get_logger().info('service not available, waiting again...')

        request = PathSrv.Request()
        request.path.fly_now = True
        request.path.header.frame_id = "fcu_untilted"

        p1 = Reference()
        p1.position.x = 0.0
        p1.position.y = 0.0
        p1.position.z = 0.0
        p1.heading = 0.0

        p2 = Reference()
        p2.position.x = 10.0
        p2.position.y = 0.0
        p2.position.z = 0.0
        p2.heading = 0.0

        p3 = Reference()
        p3.position.x = 10.0
        p3.position.y = 10.0
        p3.position.z = 0.0
        p3.heading = 0.0

        p4 = Reference()
        p4.position.x = 0.0
        p4.position.y = 10.0
        p4.position.z = 0.0
        p4.heading = 0.0

        request.path.points = [
                p1,
                p2,
                p3,
                p4,
                p1,
                ];

        self.get_logger().info('Calling service')

        future = self.client.call_async(request)
        future.add_done_callback(self.doneCallback)

    def doneCallback(self, future):

        try:
            response = future.result()
            print("response: {}".format(response))
        except Exception as e:
            print("e: {}".format(e))

        rclpy.shutdown()

def main(args=None):

    rclpy.init(args=args)

    node = Goto()

    rclpy.spin(node)

if __name__ == '__main__':
    main()
