import logging
import sys
import time
from threading import Event
from threading import Thread

import cflib.crtp
from cflib.crazyflie import Crazyflie
from cflib.crazyflie.log import LogConfig
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
from cflib.positioning.motion_commander import MotionCommander
from cflib.utils import uri_helper
import pid_controller as pid

from scipy.spatial.transform import Rotation

import numpy as np

from NatNetClient import NatNetClient
# from util import quaternion_to_euler_angle_vectorized1
import socket


uri = uri_helper.uri_from_env(default='radio://0/82/2M/E7E7E7E7E7')
rigid_body_id = 13
logging.basicConfig(level=logging.ERROR)

positions = {}
rotations = {}

def console_callback(text: str):
    '''A callback to run when we get console text from Crazyflie'''
    # We do not add newlines to the text received, we get them from the
    # Crazyflie at appropriate places.
    print(text, end='')

def receive_rigid_body_frame(robot_id, position, rotation_quaternion):
    positions[robot_id] = position
    # rot = Rotation.from_quat(rotation_quaternion)
    rotations[robot_id] = rotation_quaternion

def param_callback(name, value):
    print('The crazyflie has parameter ' + name + ' set at number: ' + value)

def set_param(cf, groupstr, namestr, value):
    full_name = groupstr+"."+namestr
    cf.param.add_update_callback(group=groupstr, name = namestr, cb = param_callback)
    # time.sleep(1)
    cf.param.set_value(full_name, value)
    # time.sleep(1)

def _connected(uri):
    print("Connected!")

def _connection_failed(self, link_uri, msg):
    """Callback when connection initial connection fails (i.e no Crazyflie
    at the specified address)"""
    print('Connection to %s failed: %s' % (uri, msg))

def _connection_lost(uri, msg):
    """Callback when disconnected after a connection has been made (i.e
    Crazyflie moves out of range)"""
    print('Connection to %s lost: %s' % (uri, msg))

def _disconnected(self, link_uri):
    """Callback when the Crazyflie is disconnected (called in all cases)"""
    print('Disconnected from %s' % uri)

def limitThrust(thrust):
    if thrust >= 65500:
        thrust = 65500
    if thrust <= 0:
        thrust = 0
    return thrust

def initialize_optitrack(rigid_body_id):
    # clientAddress = socket.gethostbyname(socket.gethostname())
    clientAddress = "192.168.0.5"
    optitrackServerAddress = "192.168.0.4"

    streaming_client = NatNetClient()
    streaming_client.set_client_address(clientAddress)
    streaming_client.set_server_address(optitrackServerAddress)
    streaming_client.set_use_multicast(True)

    streaming_client.rigid_body_listener = receive_rigid_body_frame

    is_running = streaming_client.run()

    time.sleep(2)

    if is_running and streaming_client.connected():
        print("Connected to Optitrack")
        return streaming_client
    else:
        print("Not connected to Optitrack")
        assert False

def initialize_crazyflie():
    cflib.crtp.init_drivers()
    cf = Crazyflie(rw_cache='./cache')
    # cf.commander.set_client_xmode(True)

    cf.connected.add_callback(_connected)
    cf.disconnected.add_callback(_disconnected)
    cf.connection_failed.add_callback(_connection_failed)
    cf.connection_lost.add_callback(_connection_lost)
    cf.console.receivedChar.add_callback(console_callback)
    print('Connecting to crazyflie at %s' % uri)

    cf.open_link(uri)
    return cf

if __name__ == '__main__':
    try:
        streaming_client = initialize_optitrack(rigid_body_id)
        # time.sleep(2)

        # print(rotations.keys())

        p_r = np.array(positions[rigid_body_id][0:2])
        cf = initialize_crazyflie()

        # Unlock startup thrust protection
        cf.commander.send_setpoint(0, 0, 0, 0)

        # set_param(cf, 'motorPowerSet', 'enable', 1)
        # set_param(cf, 'stabilizer', 'controller', 2)

        # CONSTANT DEF
        count = 0                   # Counter
        theta_r = 0                 # Rotation of Robot
        v_des = 0                   # Desired Velocity
        p_d = np.array([0, 0, 2])     # Desired Position
        r = .2                      # Destination Threshold
        # kp = 0.1                    # Proportional Gain
        # kd = 0.1                    # Derivative Gain
        mass = 0.04                 # the mass of the quadrotor in kg
        f_b = 0.25                   # the net lift force of the balloon in N
        g = 9.81                    # Accelleration due to gravity
        e3 = np.array([0,0,1])      # Z unit vector
        yaw_d = 0                   # Desired Yaw

        # We want to control x, y, z, and yaw
        pid_x   =   pid.PID(1.5, 0.0, 1.0)
        pid_y   =   pid.PID(1.5, 0.0, 1.0)
        pid_z   =   pid.PID(3.0, 0.2, 2.0)
        # pid_yaw =   pid.PID(16000, 10, 14500)

        current_time = time.time()
        print("Into the loop now!")
        while(True):
            # Position
            p_r = np.array(positions[rigid_body_id][0:3])

            # # Velocity
            # previous_time = current_time
            # current_time = time.time()
            # dp = p_r - old_p_r
            # dt = current_time - previous_time
            # v_r = (p_r - old_p_r)/current_time - previous_time
            # err_v = v_des - v_r

            # positional error:
            # a nice PID updater that takes care of the errors including
            # proportional, integral, and derivative terms
            err_p = p_d - p_r
            fx = pid_x.Update(err_p[0])
            fy = pid_y.Update(err_p[1])
            fz = pid_z.Update(err_p[2])

            # desired force that we want the robot to generate in the {world frame}
            fd = np.array([fx, fy, fz]) + (mass * g - f_b) * e3

            # orientation of the robot
            rot = Rotation.from_quat(rotations[rigid_body_id][0:4])
            # in the format of SO(3)
            rot_SO3 = rot.as_matrix()
            # yaw angle
            # yaw_r = rot.as_euler("xyz")[2]
            # err_yaw = yaw_d - yaw_r

            # desired force that we want the robot to generate in the {body frame}
            # fd_b = rot_SO3.T.dot(fd)

            # desired torque along yaw
            # tau_z = pid_yaw.Update(err_yaw)

            normfd = np.linalg.norm(fd) # Magnitude

            xid = np.array([np.cos(yaw_d), np.sin(yaw_d), 0]) # intermediate xd
            zfd = fd/normfd

            yfd = np.cross(zfd, xid)
            normyfd = np.linalg.norm(yfd)
            yfd = yfd/normyfd

            xfd = np.cross(yfd, zfd)
            normxfd = np.linalg.norm(xfd)
            xfd = xfd/normxfd

            # Desired Rotation Matrix
            Rd = np.hstack((np.asmatrix(xfd).T, np.asmatrix(yfd).T, np.asmatrix(zfd).T))
            rd = Rotation.from_matrix(Rd)
            # print( np.linalg.det(Rd))
            # assert np.allclose(np.linalg.det(Rd), 1)
            #
            # Desired Roll Pitch and Yaw angles
            angles = rd.as_euler('xyz', degrees = True)
            # print(angles)
            roll = angles[0]
            pitch = angles[1]
            yaw = angles[2]

            # desired thrust
            thrust_correction = fd.dot(rot_SO3.dot(e3))
            cf.commander.send_setpoint(roll, pitch, yaw, limitThrust(int(8000*thrust_correction)))

            count += 1
            if count >= 500:
                count = 0
                # print("Orientation of the Robot: ", p_r, "Rotation:", theta_r,"\nDesitnation: ",p_d)
                print("Current thrust = ", limitThrust(8000*thrust_correction))
                print("Current Pitch = ", pitch)
                print("Current Roll = ", roll)
                print("Current Yaw = ", yaw, "\n\n")
                # print(rot.as_euler("xyz"), fd, fd_b)
                # print(limitThrust(-fd_b[0]-fd_b[1]+fd_b[2]-tau_z),
                #       limitThrust(fd_b[0]-fd_b[1]+fd_b[2]+tau_z),
                #       limitThrust(fd_b[0]+fd_b[1]+fd_b[2]-tau_z),
                #       limitThrust(-fd_b[0]+fd_b[1]+fd_b[2]+tau_z))
                # print(yaw_r)

            # set_param(cf, 'motorPowerSet', 'm1', limitThrust(-fd_b[0]-fd_b[1]+fd_b[2]-tau_z))
            # set_param(cf, 'motorPowerSet', 'm2', limitThrust(fd_b[0]-fd_b[1]+fd_b[2]+tau_z))
            # set_param(cf, 'motorPowerSet', 'm3', limitThrust(fd_b[0]+fd_b[1]+fd_b[2]-tau_z))
            # set_param(cf, 'motorPowerSet', 'm4', limitThrust(-fd_b[0]+fd_b[1]+fd_b[2]+tau_z))

            # if distance <= r:
            # cf.commander.send_setpoint(0, 30000, 0, 0)
            #     print("Landing...")
            #     time.sleep(5)
            #     break
    except KeyboardInterrupt:
        set_param(cf, 'motorPowerSet', 'enable', 0)
        cf.commander.send_setpoint(0, 0, 0, 0)
        # time.sleep(0.1)
        print("Completed")