#!/usr/bin/python3

import rclpy
from rclpy.node import Node
from mrs_msgs.srv import Vec4

class Goto(Node):

    def __init__(self):

        super().__init__('goto')

        self.get_logger().info('ROS2 node initialized')

        self.get_logger().info('Setting up the client')

        self.client = self.create_client(Vec4, "/uav2/control_manager/goto")

        self.timer = self.create_timer(0.1, self.doAction)

        self.get_logger().info('__init__ finished')

    def doAction(self):

        self.get_logger().info('doing the action')

        self.timer.cancel()

        while not self.client.wait_for_service(timeout_sec=3.0):
            self.get_logger().info('service not available, waiting again...')

        request = Vec4.Request()
        request.goal[0] = -10.0
        request.goal[1] = 0.0
        request.goal[2] = 2.0
        request.goal[3] = 1.5

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
