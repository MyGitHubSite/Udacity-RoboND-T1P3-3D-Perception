#!/usr/bin/env python

# Import modules
import numpy as np
import sklearn
from sklearn.preprocessing import LabelEncoder
import pickle
from sensor_stick.srv import GetNormals
from sensor_stick.features import compute_color_histograms
from sensor_stick.features import compute_normal_histograms
from visualization_msgs.msg import Marker
from sensor_stick.marker_tools import *
from sensor_stick.msg import DetectedObjectsArray
from sensor_stick.msg import DetectedObject
from sensor_stick.pcl_helper import *

import rospy
import tf
from geometry_msgs.msg import Pose
from std_msgs.msg import Float64
from std_msgs.msg import Int32
from std_msgs.msg import String
from pr2_robot.srv import *
from rospy_message_converter import message_converter
import yaml

# Helper function to get surface normals
def get_normals(cloud):
    get_normals_prox = rospy.ServiceProxy('/feature_extractor/get_normals', GetNormals)
    return get_normals_prox(cloud).cluster

# Helper function to create a yaml friendly dictionary from ROS messages
def make_yaml_dict(test_scene_num, arm_name, object_name, pick_pose, place_pose):
    yaml_dict = {}
    yaml_dict["test_scene_num"] = test_scene_num.data
    yaml_dict["arm_name"]  = arm_name.data
    yaml_dict["object_name"] = object_name.data
    yaml_dict["pick_pose"] = message_converter.convert_ros_message_to_dictionary(pick_pose)
    yaml_dict["place_pose"] = message_converter.convert_ros_message_to_dictionary(place_pose)
    return yaml_dict

# Helper function to output to yaml file
def send_to_yaml(yaml_filename, dict_list):
    data_dict = {"object_list": dict_list}
    with open(yaml_filename, 'w') as outfile:
        yaml.dump(data_dict, outfile, default_flow_style=False)

# Callback function for your Point Cloud Subscriber
def pcl_callback(pcl_msg):

    print("\nStart pcl_callback,...")

    # Exercise-2 TODOs:

    # TODO: Convert ROS msg to PCL data
    cloud = ros_to_pcl(pcl_msg)

    # Statistical Outlier Filtering
    fil = cloud.make_statistical_outlier_filter()
    fil.set_mean_k(10)   
    fil.set_std_dev_mul_thresh(0.5)   
    cloud_filtered = fil.filter()

    # TODO: Voxel Grid Downsampling
    # Create a VoxelGrid filter object for our input point cloud
    vox = cloud_filtered.make_voxel_grid_filter()

    # Choose a voxel (also known as leaf) size
    LEAF_SIZE = 0.01   

    # Set the voxel (or leaf) size  
    vox.set_leaf_size(LEAF_SIZE, LEAF_SIZE, LEAF_SIZE)

    # Call the filter function to obtain the resultant downsampled point cloud
    cloud_filtered = vox.filter()

    # TODO: PassThrough Filter
    # Create a PassThrough filter object.

    # Assign axis and range to the passthrough filter object.
    filter_axis = 'z'
    passthrough = cloud_filtered.make_passthrough_filter()
    passthrough.set_filter_field_name(filter_axis)
    axis_min = 0.6   
    axis_max = 1.1    
    passthrough.set_filter_limits(axis_min, axis_max)
    cloud_filtered = passthrough.filter()

    # Assign axis and range to the passthrough filter object.
    filter_axis = 'y'
    passthrough = cloud_filtered.make_passthrough_filter()
    passthrough.set_filter_field_name(filter_axis)
    axis_min = -0.5
    axis_max = 0.5
    passthrough.set_filter_limits(axis_min, axis_max)
    cloud_filtered = passthrough.filter()

    # TODO: RANSAC Plane Segmentation
    # Create the segmentation object
    seg = cloud_filtered.make_segmenter()

    # Set the model you wish to fit 
    seg.set_model_type(pcl.SACMODEL_PLANE)
    seg.set_method_type(pcl.SAC_RANSAC)

    # Max distance for a point to be considered fitting the model
    max_distance = 0.01
    seg.set_distance_threshold(max_distance)

    # Call the segment function to obtain set of inlier indices and model coefficients
    inliers, coefficients = seg.segment()

    # TODO: Extract inliers and outliers
    cloud_table = cloud_filtered.extract(inliers, negative=False)
    cloud_objects = cloud_filtered.extract(inliers, negative=True)

    # TODO: Euclidean Clustering
    # Use only spatial information:
    white_cloud = XYZRGB_to_XYZ(cloud_objects)
    tree = white_cloud.make_kdtree()

    # Create a cluster extraction object
    ec = white_cloud.make_EuclideanClusterExtraction()

    # Set tolerances for distance threshold as well as minimum and maximum cluster size (in points)
    ec.set_ClusterTolerance(0.05)
    ec.set_MinClusterSize(50)
    ec.set_MaxClusterSize(5000)

    # Search the k-d tree for clusters
    ec.set_SearchMethod(tree)

    # Extract indices for each of the discovered clusters
    cluster_indices = ec.Extract()

    # TODO: Create Cluster-Mask Point Cloud to visualize each cluster separately
    #Assign a color corresponding to each segmented object in scene
    cluster_color = get_color_list(len(cluster_indices))
    color_cluster_point_list = []  

    for j, indices in enumerate(cluster_indices):
        for i, indice in enumerate(indices):
            color_cluster_point_list.append([white_cloud[indice][0],
                                             white_cloud[indice][1],
                                             white_cloud[indice][2],
                                             rgb_to_float(cluster_color[j])])

    #Create new cloud containing all clusters, each with unique color
    cloud_cluster = pcl.PointCloud_PointXYZRGB()
    cloud_cluster.from_list(color_cluster_point_list)

    # TODO: Convert PCL data to ROS messages
    ros_cloud_objects = pcl_to_ros(cloud_objects)
    ros_cloud_table = pcl_to_ros(cloud_table)
    ros_cloud_cluster = pcl_to_ros(cloud_cluster)

    # TODO: Publish ROS messages
    pcl_objects_pub.publish(ros_cloud_objects)
    pcl_table_pub.publish(ros_cloud_table)
    pcl_cluster_pub.publish(ros_cloud_cluster)

    # Exercise-3 TODOs: 

    # Classify the clusters! (loop through each detected cluster one at a time)
    detected_objects_labels = []
    detected_objects = []

    for index, pts_list in enumerate(cluster_indices):
        # Grab the points for the cluster
        pcl_cluster = cloud_objects.extract(pts_list)
        ros_cluster = pcl_to_ros(pcl_cluster)

        # Compute the associated feature vector
        chists = compute_color_histograms(ros_cluster, using_hsv=True)
        normals = get_normals(ros_cluster)
        nhists = compute_normal_histograms(normals)
        feature = np.concatenate((chists, nhists))

        # Make the prediction and add it to detected_objects_labels list
        prediction = clf.predict(scaler.transform(feature.reshape(1,-1)))
        label = encoder.inverse_transform(prediction)[0]
        detected_objects_labels.append(label)

        # Publish a label into RViz
        label_pos = list(cloud_objects[pts_list[0]])[0:3]
        label_pos[2] += .4
        object_markers_pub.publish(make_label(label,label_pos, index))

        # Add the detected object to the list of detected objects.
        do = DetectedObject()
        do.label = label
        do.cloud = ros_cluster
        detected_objects.append(do)    

    rospy.loginfo('Detected {} objects: {}'.format(len(detected_objects_labels), detected_objects_labels))

    # Publish the list of detected objects
    detected_objects_pub.publish(detected_objects)

    # Invoke your pr2_mover() function within pcl_callback()
    try:
	print ('\nCalling pr2_mover')
        pr2_mover(detected_objects)
    except rospy.ROSInterruptException:
        pass

    print ('\nShutting down')	
    rospy.signal_shutdown("Completed")

# Function to load parameters and request PickPlace service
def pr2_mover(object_list):     

    # TODO: Initialize variables
    dict_list = []
    pick_centroids = [] # to be list of tuples (x, y, z)

    test_scene_num = Int32()
    test_scene_num.data = 1

    # TODO: Get/Read parameters
    pick_list_param = rospy.get_param('/object_list')
    dropbox_param = rospy.get_param('/dropbox')

    # TODO: Parse parameters into individual variables
    dropbox = {}
    for p in dropbox_param:
        dropbox[p['name']] = p['position']

    # TODO: Rotate PR2 in place to capture side tables for the collision map

    # TODO: Loop through the pick list
    for pick in pick_list_param:
	print (pick_list_param)

	pick_name = pick['name']
	pick_group = pick['group']	

        print("\nLooking for [{}] to be placed in [{}] box.".format(pick_name, pick_group))

        object_name = String()
        object_name.data = pick_name

        # TODO: Get the PointCloud for a given object and obtain it's centroid

        # Index of the object to be picked in the `detected_objects` list
        pick_cloud = None
        for i, detected_object in enumerate(object_list):
            if(detected_object.label == pick_name):
                pick_cloud = detected_object.cloud
            
        if(pick_cloud == None):
            print("ERROR:::: {} not found in the detected object list".format(pick_name))
            continue

        points_arr = ros_to_pcl(pick_cloud).to_array()
        pick_centroid = np.mean(points_arr, axis=0)[:3]
        pick_centroids.append(pick_centroid)

        print("\nCentroid found : {}".format(pick_centroid))

        pick_pose = Pose()
        pick_pose.position.x = float(pick_centroid[0])   
        pick_pose.position.y = float(pick_centroid[1])   
        pick_pose.position.z = float(pick_centroid[2])   

        # TODO: Assign the arm to be used for pick_place
        arm_name = String()
        if pick['group'] == 'red':
            arm_name.data = 'left'
        elif pick['group'] == 'green':
            arm_name.data = 'right'
        else:
            print "ERROR, group must be green or red!"

        # TODO: Create 'place_pose' for the object
        place_pose = Pose()
        place_pose.position.x = dropbox[arm_name.data][0]
        place_pose.position.y = dropbox[arm_name.data][1]
        place_pose.position.z = dropbox[arm_name.data][2]

        # TODO: Create a list of dictionaries (made with make_yaml_dict()) for later output to yaml format
        yaml_dict = make_yaml_dict(test_scene_num, arm_name, object_name, pick_pose, place_pose)
        dict_list.append(yaml_dict)

        # Wait for 'pick_place_routine' service to come up
        rospy.wait_for_service('pick_place_routine')

        try:
            pick_place_routine = rospy.ServiceProxy('pick_place_routine', PickPlace)

            # TODO: Insert your message variables to be sent as a service request
            resp = pick_place_routine(test_scene_num, object_name, arm_name, pick_pose, place_pose)
            print ("Response: ",resp.success)

        except rospy.ServiceException, e:
            print "Service call failed: %s"%e

    # TODO: Output your request parameters into output yaml file
    print ('\nOutputting to YAML')
    yaml_filename = "output_{}.yaml".format(test_scene_num.data)
    send_to_yaml(yaml_filename, dict_list)

if __name__ == '__main__':

    # TODO: ROS node initialization
    rospy.init_node('clustering', anonymous=True)

    # TODO: Create Subscribers
    #pcl_sub = rospy.Subscriber("/sensor_stick/point_cloud", pc2.PointCloud2, pcl_callback, queue_size=1)
    pcl_sub = rospy.Subscriber("/pr2/world/points", pc2.PointCloud2, pcl_callback, queue_size=1)

    # TODO: Create Publishers
    pcl_objects_pub = rospy.Publisher("/pcl_objects", PointCloud2, queue_size=1)
    pcl_table_pub = rospy.Publisher("/pcl_table", PointCloud2, queue_size=1)
    pcl_cluster_pub = rospy.Publisher("/pcl_cluster", PointCloud2, queue_size=1)
    object_markers_pub = rospy.Publisher("/object_markers", Marker, queue_size=1)
    detected_objects_pub = rospy.Publisher("/detected_objects", DetectedObjectsArray, queue_size=1)

    # Load Model From disk
    model = pickle.load(open('model1.sav', 'rb'))
    clf = model['classifier']
    encoder = LabelEncoder()
    encoder.classes_ = model['classes']
    scaler = model['scaler']

    # Initialize color_list
    get_color_list.color_list = []

    # TODO: Spin while node is not shutdown
    while not rospy.is_shutdown():
     rospy.spin()
