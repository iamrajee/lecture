#!/usr/bin/python

import numpy
import rospy
import random
from std_msgs.msg import Header
from sensor_msgs.msg import JointState
from geometry_msgs.msg import TransformStamped, Transform, Quaternion, Vector3, Point
from visualization_msgs.msg import Marker, InteractiveMarker, InteractiveMarkerControl
from tf import transformations as tf
from interactive_markers.interactive_marker_server import InteractiveMarkerServer
from robot_model import RobotModel, Joint
from markers import frame

class MyInteractiveMarkerServer(InteractiveMarkerServer):
    def __init__(self, name, T):
        InteractiveMarkerServer.__init__(self, name)
        self.target = numpy.eye(4)
        self.create_interactive_marker(T)

    def create_interactive_marker(self, T):
        im = InteractiveMarker()
        im.header.frame_id = "world"
        im.name = "target"
        im.description = "Controller Target"
        im.scale = 0.2
        im.pose.position = Point(*T[0:3,3])
        im.pose.orientation = Quaternion(*tf.quaternion_from_matrix(T))
        self.process_marker_feedback(im)  # set target to initial pose

        # Create a control to move a (sphere) marker around with the mouse
        control = InteractiveMarkerControl()
        control.name = "move_3d"
        control.interaction_mode = InteractiveMarkerControl.MOVE_3D
        control.markers.extend(frame(numpy.eye(4), scale=0.1, frame_id='').markers)
        im.controls.append(control)

        # Create arrow controls to move the marker
        for dir in 'xyz':
            control = InteractiveMarkerControl()
            control.name = "move_" + dir
            control.interaction_mode = InteractiveMarkerControl.MOVE_AXIS
            control.orientation.x = 1 if dir == 'x' else 0
            control.orientation.y = 1 if dir == 'y' else 0
            control.orientation.z = 1 if dir == 'z' else 0
            control.orientation.w = 1
            im.controls.append(control)

        # Create controls to rotate the marker
        for dir in 'xyz':
            control = InteractiveMarkerControl()
            control.name = "rotate_" + dir
            control.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
            control.orientation.x = 1 if dir == 'x' else 0
            control.orientation.y = 1 if dir == 'y' else 0
            control.orientation.z = 1 if dir == 'z' else 0
            control.orientation.w = 1
            im.controls.append(control)

        # Add the marker to the server and indicate that processMarkerFeedback should be called
        self.insert(im, self.process_marker_feedback)

        # Publish all changes
        self.applyChanges()

    def process_marker_feedback(self, feedback):
        q = feedback.pose.orientation
        p = feedback.pose.position
        self.target = tf.quaternion_matrix(numpy.array([q.x, q.y, q.z, q.w]))
        self.target[0:3,3] = numpy.array([p.x, p.y, p.z])


class Controller(object):
    damping = 0.1
    threshold = 0.1

    def __init__(self, pose=TransformStamped(header=Header(frame_id='panda_link8'), child_frame_id='target',
                                             transform=Transform(rotation=Quaternion(*tf.quaternion_about_axis(numpy.pi/4, [0,0,1])),
                                                                 translation=Vector3(0,0,0.105)))):

        self.robot = RobotModel()
        self.robot._add(Joint(pose))  # add a fixed end-effector transform
        self.pub = rospy.Publisher('/target_joint_states', JointState, queue_size=10)
        self.joint_msg = JointState()
        self.joint_msg.name = [j.name for j in self.robot.active_joints]
        self.joint_msg.position = numpy.asarray([(j.min+j.max)/2 + 0.1*(j.max-j.min)*random.uniform(0,1) for j in self.robot.active_joints])
        self.target_link = pose.child_frame_id
        self.T, self.J = self.robot.fk(self.target_link, dict(zip(self.joint_msg.name, self.joint_msg.position)))

        self.im_server = MyInteractiveMarkerServer("controller", self.T)

    def actuate(self, q_delta):
        self.joint_msg.position += q_delta.ravel()
        self.pub.publish(self.joint_msg)
        self.T, self.J = self.robot.fk(self.target_link, dict(zip(self.joint_msg.name, self.joint_msg.position)))

    def solve(self, J, error):
        def invert_clip(s):
            return 1./s if s > self.threshold else 0.

        def invert_damp(s):
            return s/(s**2 + self.damping**2)

        def invert_smooth_clip(s):
            return s/(self.threshold**2) if s < self.threshold else 1./s

        U, S, Vt = numpy.linalg.svd(J)
        rank = min(U.shape[0], Vt.shape[1])
        for i in range(rank):
            S[i] = invert_smooth_clip(S[i])

        return numpy.dot(Vt.T[:,0:rank], S * U.T.dot(numpy.array(error)))

    @staticmethod
    def position_error(T_tgt, T_cur):
        # transform error vector from base frame to end-effector frame
        return T_cur[0:3,0:3].T.dot(T_tgt[0:3,3]-T_cur[0:3,3])

    @staticmethod
    def orientation_error(T_tgt, T_cur):
        delta = numpy.eye(4)
        delta[0:3,0:3] = T_cur[0:3,0:3].T.dot(T_tgt[0:3,0:3])
        angle, axis, _ = tf.rotation_from_matrix(delta)
        return angle * axis

    def position_control(self):
        v = self.position_error(self.im_server.target, self.T)
        q_delta = self.solve(self.J[0:3,:], v)
        self.actuate(q_delta)

    def pose_control(self):
        v = self.position_error(self.im_server.target, self.T)
        w = self.orientation_error(self.im_server.target, self.T)
        q_delta = self.solve(self.J, numpy.block([0.1*v, 0.1*w]))
        self.actuate(q_delta)

rospy.init_node('ik')
c = Controller()
rate = rospy.Rate(50)
while not rospy.is_shutdown():
    c.pose_control()
    rate.sleep()
