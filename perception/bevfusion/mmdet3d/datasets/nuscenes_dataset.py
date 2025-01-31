import tempfile
from os import path as osp
import os #! H95
from typing import Any, Dict

import json
import mmcv
import numpy as np
import pyquaternion
import torch
from nuscenes.utils.data_classes import Box as NuScenesBox
from pyquaternion import Quaternion

from mmdet.datasets import DATASETS

from ..core.bbox import LiDARInstance3DBoxes
from .custom_3d import Custom3DDataset

from transforms3d.euler import euler2mat, quat2euler, euler2quat
from transforms3d.quaternions import quat2mat, mat2quat

from projects.mean_ap import eval_map
#from detection_util import apply_nms_det

class MyConfig():
    def __init__(self, motion_state = False, pred_type = 'motion'):
        self.motion_state = motion_state
        self.pred_type = pred_type


@DATASETS.register_module()
class NuScenesDataset(Custom3DDataset):
    r"""NuScenes Dataset.

    This class serves as the API for experiments on the NuScenes Dataset.

    Please refer to `NuScenes Dataset <https://www.nuscenes.org/download>`_
    for data downloading.

    Args:
        ann_file (str): Path of annotation file.
        pipeline (list[dict], optional): Pipeline used for data processing.
            Defaults to None.
        dataset_root (str): Path of dataset root.
        classes (tuple[str], optional): Classes used in the dataset.
            Defaults to None.
        load_interval (int, optional): Interval of loading the dataset. It is
            used to uniformly sample the dataset. Defaults to 1.
        with_velocity (bool, optional): Whether include velocity prediction
            into the experiments. Defaults to True.
        modality (dict, optional): Modality to specify the sensor data used
            as input. Defaults to None.
        box_type_3d (str, optional): Type of 3D box of this dataset.
            Based on the `box_type_3d`, the dataset will encapsulate the box
            to its original format then converted them to `box_type_3d`.
            Defaults to 'LiDAR' in this dataset. Available options includes.
            - 'LiDAR': Box in LiDAR coordinates.
            - 'Depth': Box in depth coordinates, usually for indoor dataset.
            - 'Camera': Box in camera coordinates.
        filter_empty_gt (bool, optional): Whether to filter empty GT.
            Defaults to True.
        test_mode (bool, optional): Whether the dataset is in test mode.
            Defaults to False.
        eval_version (bool, optional): Configuration version of evaluation.
            Defaults to  'detection_cvpr_2019'.
        use_valid_flag (bool): Whether to use `use_valid_flag` key in the info
            file as mask to filter gt_boxes and gt_names. Defaults to False.
    """
    NameMapping = {
        "movable_object.barrier": "barrier",
        "vehicle.bicycle": "bicycle",
        "vehicle.bus.bendy": "bus",
        "vehicle.bus.rigid": "bus",
        "vehicle.car": "car",
        "vehicle.construction": "construction_vehicle",
        "vehicle.motorcycle": "motorcycle",
        "human.pedestrian.adult": "pedestrian",
        "human.pedestrian.child": "pedestrian",
        "human.pedestrian.construction_worker": "pedestrian",
        "human.pedestrian.police_officer": "pedestrian",
        "movable_object.trafficcone": "traffic_cone",
        "vehicle.trailer": "trailer",
        "vehicle.truck": "truck",
    }
    DefaultAttribute = {
        "car": "vehicle.parked",
        "pedestrian": "pedestrian.moving",
        "trailer": "vehicle.parked",
        "truck": "vehicle.parked",
        "bus": "vehicle.moving",
        "motorcycle": "cycle.without_rider",
        "construction_vehicle": "vehicle.parked",
        "bicycle": "cycle.without_rider",
        "barrier": "",
        "traffic_cone": "",
    }
    AttrMapping = {
        "cycle.with_rider": 0,
        "cycle.without_rider": 1,
        "pedestrian.moving": 2,
        "pedestrian.standing": 3,
        "pedestrian.sitting_lying_down": 4,
        "vehicle.moving": 5,
        "vehicle.parked": 6,
        "vehicle.stopped": 7,
    }
    AttrMapping_rev = [
        "cycle.with_rider",
        "cycle.without_rider",
        "pedestrian.moving",
        "pedestrian.standing",
        "pedestrian.sitting_lying_down",
        "vehicle.moving",
        "vehicle.parked",
        "vehicle.stopped",
    ]
    # https://github.com/nutonomy/nuscenes-devkit/blob/57889ff20678577025326cfc24e57424a829be0a/python-sdk/nuscenes/eval/detection/evaluate.py#L222 # noqa
    ErrNameMapping = {
        "trans_err": "mATE",
        "scale_err": "mASE",
        "orient_err": "mAOE",
        "vel_err": "mAVE",
        "attr_err": "mAAE",
    }
    CLASSES = (
        "car",
        "truck",
        "trailer",
        "bus",
        "construction_vehicle",
        "bicycle",
        "motorcycle",
        "pedestrian",
        "traffic_cone",
        "barrier",
    )

    def __init__(
        self,
        ann_file,
        pipeline=None,
        dataset_root=None,
        object_classes=None,
        map_classes=None,
        load_interval=1,
        with_velocity=True,
        modality=None,
        box_type_3d="LiDAR",
        filter_empty_gt=True,
        test_mode=False,
        eval_version="detection_cvpr_2019",
        use_valid_flag=False,
    ) -> None:
        self.load_interval = load_interval    #! when train: 1
        self.use_valid_flag = use_valid_flag  #! when train: True
        super().__init__(
            dataset_root=dataset_root,
            ann_file=ann_file,
            pipeline=pipeline,
            classes=object_classes,
            modality=modality,
            box_type_3d=box_type_3d,
            filter_empty_gt=filter_empty_gt,
            test_mode=test_mode,
        )
        self.map_classes = map_classes

        self.with_velocity = with_velocity
        self.eval_version = eval_version
        from nuscenes.eval.detection.config import config_factory

        self.eval_detection_configs = config_factory(self.eval_version)
        if self.modality is None:
            self.modality = dict(
                use_camera=False,
                use_lidar=True,
                use_radar=False,
                use_map=False,
                use_external=False,
            )
        
        #! H95: 19, 20
        # self.single_pred_path = "./data/uav3d/v1.0/h95_sample_sum/single_pred.json"
        # with open(self.single_pred_path) as f:
        #     self.single_pred_all = json.load(f)
        
        # self.sample_data_path = "./data/uav3d/v1.0/v1.0-trainval/sample_data.json"
        # with open(self.sample_data_path, 'r') as f:
        #     self.sample_data_all = json.load(f)
        
        # self.calibrated_sensor_path = "./data/uav3d/v1.0/v1.0-trainval/calibrated_sensor.json"
        # with open(self.calibrated_sensor_path, 'r') as f:
        #     self.calibrated_sensor_all = json.load(f)
        
        #! h95_add_21&22: 将读取文件放在初始化部分
        # self.single_preds = {}
        # self.single_pred_path = "./data/uav3d/v1.0/h95_each_sample_single_pred_ego_coordinate"
        # for mm in os.listdir(self.single_pred_path):
        #     token = mm[:-5]
        #     full_path = os.path.join(self.single_pred_path, mm)
        #     with open(full_path, 'r') as f:
        #         single_pred = json.load(f)
        #     single_pred = np.array(single_pred).astype(np.float32)
        #     self.single_preds[token] = single_pred

        #! 23, 24
        #* 20250125, 01:24: 换成新的写法
        print(f"Start to load single_pred file.")
        self.single_pred_path = "/mnt/storage/hjw/code/uav3d/perception/bevfusion/h95_2324.json"
        with open(self.single_pred_path) as f:
            self.single_preds = json.load(f)
        print(f"Successfully load single_pred file.")
        # print(self.single_preds["d081ede3282947da98274a7268787c0f"])
        # raise Exception
        #* 经过验证, len==5
        # for k, v in self.single_preds.items():
        #     if len(v) != 5:
        #         raise Exception("len!=5")
        # raise Exception("len==5")

        #* 老的写法
        # self.single_pred_path = "./data/uav3d/v1.0/h95_single_pred_tensor"
        # self.single_preds = {}
        # sample_tokens = []
        # for record in os.listdir(self.single_pred_path):
        #     # record: a74bf49d1dc54d84ae5288a2940ba409.json
        #     sample_token = record[0:-5]
        #     sample_tokens.append(sample_token)
        #     # print(sample_token)
        #     full_path = os.path.join(self.single_pred_path, record)
        #     with open(full_path, "r") as f:
        #         single_pred = json.load(f) # list
            
        #     # max_N = max(len(item) for item in single_pred)
        #     # padded_data = []
        #     # for kk in single_pred:
        #     #     padded_item = kk + [[9995] * 8] * (max_N - len(kk)) # 用9995填充
        #     #     padded_data.append(padded_item)
        #     # single_pred = torch.tensor(padded_data) #! [5, 37, 8]
        #     #! 以list的形式回传
        #     self.single_preds[sample_token] = single_pred
        # kkk = set(sample_tokens) # len=17000
        # print(len(kkk))
        # print(len(sample_tokens)) # len=17000
        # print("977602f697fc49fb96baf52cee976192" in sample_tokens) # True
        # raise Exception


    def get_cat_ids(self, idx):
        """Get category distribution of single scene.

        Args:
            idx (int): Index of the data_info.

        Returns:
            dict[list]: for each category, if the current scene
                contains such boxes, store a list containing idx,
                otherwise, store empty list.
        """
        info = self.data_infos[idx]
        if self.use_valid_flag:
            mask = info["valid_flag"]
            gt_names = set(info["gt_names"][mask])
        else:
            gt_names = set(info["gt_names"])

        cat_ids = []
        for name in gt_names:
            if name in self.CLASSES:
                cat_ids.append(self.cat2id[name])
        return cat_ids

    def load_annotations(self, ann_file):
        """Load annotations from ann_file.

        Args:
            ann_file (str): Path of the annotation file.

        Returns:
            list[dict]: List of annotations sorted by timestamps.
        """
        data = mmcv.load(ann_file)
        data_infos = list(sorted(data["infos"], key=lambda e: e["timestamp"]))
        data_infos = data_infos[:: self.load_interval]
        #! when train, data_infos=list, len = 14000
        #!     data_infos[0].keys(): ['lidar_path', 'token', 'sweeps', 'cams', 'lidar2ego_translation', 'lidar2ego_rotation', 'ego2global_translation', 'ego2global_rotation', 'timestamp', 'gt_boxes', 'gt_names', 'gt_velocity', 'num_lidar_pts', 'num_radar_pts', 'valid_flag', 'location']
        #!         data_infos[0]['cams']: dict, keys(25个) = ['CAMERA_FRONT_id_0', 'CAMERA_BACK_id_0', 'CAMERA_LEFT_id_0', 'CAMERA_RIGHT_id_0', 'CAMERA_BOTTOM_id_0', 'CAMERA_FRONT_id_1', 'CAMERA_BACK_id_1', 'CAMERA_LEFT_id_1', 'CAMERA_RIGHT_id_1', 'CAMERA_BOTTOM_id_1', 'CAMERA_FRONT_id_2', 'CAMERA_BACK_id_2', 'CAMERA_LEFT_id_2', 'CAMERA_RIGHT_id_2', 'CAMERA_BOTTOM_id_2', 'CAMERA_FRONT_id_3', 'CAMERA_BACK_id_3', 'CAMERA_LEFT_id_3', 'CAMERA_RIGHT_id_3', 'CAMERA_BOTTOM_id_3', 'CAMERA_FRONT_id_4', 'CAMERA_BACK_id_4', 'CAMERA_LEFT_id_4', 'CAMERA_RIGHT_id_4', 'CAMERA_BOTTOM_id_4']

        self.metadata = data["metadata"]
        self.version = self.metadata["version"]
        return data_infos

    def get_data_info(self, index: int) -> Dict[str, Any]:
        info = self.data_infos[index]

        data = dict(
            token=info["token"],
            sample_idx=info['token'],
            lidar_path=info["lidar_path"],
            sweeps=info["sweeps"],
            timestamp=info["timestamp"],
            location=info["location"],
        )

        #! 19, 20
        #! 这里不能这么写, 速度太慢, 要提前准备好数据, 直接加载
        # sample_token = info["token"]
        # single_pred = self.single_pred_all[sample_token]
        # calibrated_sensor_token = [mm["calibrated_sensor_token"] for mm in self.sample_data_all if mm["sample_token"]==sample_token and "BOTTOM_id_0" in mm["filename"] ]
        # assert len(calibrated_sensor_token) == 1

        # ego2global_translation = [mm["translation"] for mm in self.calibrated_sensor_all if mm["token"] == calibrated_sensor_token[0]]
        # for box in single_pred:
        #     box[0] = box[0] - ego2global_translation[0][0]
        #     box[1] = box[1] - ego2global_translation[0][1]
        # single_pred = np.array(single_pred).astype(np.float32)
        # data["single_pred"] = single_pred

        #! 新的写法: 添加single_detection在ego坐标系的预测box
        # 频繁的和文件交互: 可能很耗时间
        # sample_token = info["token"]
        # with open("./data/uav3d/v1.0/h95_each_sample_single_pred_ego_coordinate/" + sample_token + ".json", "r") as f:
        #     single_pred = json.load(f)
        # single_pred = np.array(single_pred).astype(np.float32)
        # data["single_pred"] = single_pred


        #! 23, 24: 加载的是global的box, 需要转换到ego下
        mm = self.single_preds[info["token"]]
        for ii in range(len(mm)):
            pred = mm[ii] # 
            for jj in range(len(pred)):
                pred[jj][0] -= info["ego2global_translation"][0]
                pred[jj][1] -= info["ego2global_translation"][1]
                
        # mm[:, :, 0] -= info["ego2global_translation"][0]
        # mm[:, :, 1] -= info["ego2global_translation"][1]
        data["single_pred"] = mm #! 以list形式回传



        #! 21, 22:
        # data["single_pred"] = self.single_preds[info["token"]]


        # ego to global transform
        ego2global = np.eye(4).astype(np.float32)
        ego2global[:3, :3] = Quaternion(info["ego2global_rotation"]).rotation_matrix
        ego2global[:3, 3] = info["ego2global_translation"]
        data["ego2global"] = ego2global

        # lidar to ego transform
        lidar2ego = np.eye(4).astype(np.float32)
        lidar2ego[:3, :3] = Quaternion(info["lidar2ego_rotation"]).rotation_matrix
        lidar2ego[:3, 3] = info["lidar2ego_translation"]
        data["lidar2ego"] = lidar2ego

        if self.modality["use_camera"]:
            data["image_paths"] = []
            data["lidar2camera"] = []
            data["lidar2image"] = []
            data["camera2ego"] = []
            data["camera_intrinsics"] = []
            data["camera2lidar"] = []

            num_cam = 0
            for _, camera_info in info["cams"].items():

                num_cam += 1
                if num_cam > 5:
                    break

                data["image_paths"].append(camera_info["data_path"])


                # lidar to camera transform
                lidar2camera_r = np.linalg.inv(camera_info["sensor2lidar_rotation"])
                lidar2camera_t = (
                    camera_info["sensor2lidar_translation"] @ lidar2camera_r.T
                )
                lidar2camera_rt = np.eye(4).astype(np.float32)
                lidar2camera_rt[:3, :3] = lidar2camera_r.T
                lidar2camera_rt[3, :3] = -lidar2camera_t
                data["lidar2camera"].append(lidar2camera_rt.T)

                # camera intrinsics
                camera_intrinsics = np.eye(4).astype(np.float32)

                # camera_intrinsics[:3, :3] = camera_info["camera_intrinsics"]
                camera_intrinsics[:3, :3] = camera_info["cam_intrinsic"]

                data["camera_intrinsics"].append(camera_intrinsics)

                # lidar to image transform
                # lidar2image = camera_intrinsics @ lidar2camera_rt.T
                # data["lidar2image"].append(lidar2image)

                # carla format xyz_to_y-zx
                matrix_xyz2yminuszx = np.eye(4).astype(np.float32)
                matrix_xyz2yminuszx[:3, :3] = np.array([[0,1,0],[0,0,-1], [1,0,0]])

                matrix_temp = matrix_xyz2yminuszx @ lidar2camera_rt.T
                lidar2image = camera_intrinsics @ matrix_temp
                data["lidar2image"].append(lidar2image)

                # camera to ego transform
                # camera2ego = np.eye(4).astype(np.float32)
                # camera2ego[:3, :3] = Quaternion(
                #     camera_info["sensor2ego_rotation"]
                # ).rotation_matrix
                # camera2ego[:3, 3] = camera_info["sensor2ego_translation"]
                # data["camera2ego"].append(camera2ego)

                # camera to ego transform
                camera2ego = np.eye(4).astype(np.float32)

                # carla
                angles_1 = quat2euler(camera_info["sensor2ego_rotation"])
                angles_1 = [angle * 180 / (np.pi) for angle in angles_1]
                # (roll, pitch, yaw) ---> (pitch, yaw, roll)
                camera2ego[:3, :3] = get_matrix(pitch=angles_1[1], yaw=angles_1[2], roll=angles_1[0])

                camera2ego[:3, 3] = camera_info["sensor2ego_translation"]
                data["camera2ego"].append(camera2ego)


                # camera to lidar transform
                camera2lidar = np.eye(4).astype(np.float32)
                camera2lidar[:3, :3] = camera_info["sensor2lidar_rotation"]
                camera2lidar[:3, 3] = camera_info["sensor2lidar_translation"]
                data["camera2lidar"].append(camera2lidar)

        annos = self.get_ann_info(index)
        data["ann_info"] = annos

        #! H95: 在这里, gt_bboxes_3d跟直接读取.pkl文件还是一样的
        #! e.g.: sample_token = "933662bda2c945489372ae5a4a7abd57"
        # if info["token"] == "933662bda2c945489372ae5a4a7abd57":
        #     print(annos)
        #     raise Exception
        # if info["token"] == "933662bda2c945489372ae5a4a7abd57":
        #     print(f"single_pred = {mm}")
        #     print(f"gt_boxes={annos}")
        #     #! annos["gt_boxes"]:
        #     # ([[-7.4315e+01,  7.8894e+01, -5.5465e-03,  4.1928e+00,  1.8162e+00,  1.4738e+00, -1.5712e+00,  0.0000e+00,  0.0000e+00],                                                                                          
        #     #   [ 9.8626e+00,  7.8933e+01,  7.3985e-02,  4.6110e+00,  2.2417e+00,  1.6673e+00, -1.5707e+00,  0.0000e+00,  0.0000e+00],
        #     #   [-7.6892e+01,  8.2391e+01, -2.3888e-02,  4.9742e+00,  2.0384e+00,  1.5543e+00, -1.5711e+00,  0.0000e+00,  0.0000e+00],
        #     #   [ 9.6960e+00,  6.8412e+01, -5.8792e-03,  4.7175e+00,  1.8948e+00,  1.3009e+00, -1.5711e+00,  0.0000e+00,  0.0000e+00],
        #     #   [-7.7576e+01, -3.2912e+01, -1.1668e-02,  4.1812e+00,  1.9941e+00,  1.3853e+00, -1.5749e+00,  0.0000e+00,  0.0000e+00]]
        #     raise Exception
        return data

    def get_ann_info(self, index):
        """Get annotation info according to the given index.

        Args:
            index (int): Index of the annotation data to get.

        Returns:
            dict: Annotation information consists of the following keys:

                - gt_bboxes_3d (:obj:`LiDARInstance3DBoxes`): \
                    3D ground truth bboxes
                - gt_labels_3d (np.ndarray): Labels of ground truths.
                - gt_names (list[str]): Class names of ground truths.
        """
        info = self.data_infos[index]
        # filter out bbox containing no points
        if self.use_valid_flag:
            mask = info["valid_flag"]
        else:
            # mask = info["num_lidar_pts"] > 0
            mask = info["num_lidar_pts"] > -2

        gt_bboxes_3d = info["gt_boxes"][mask]
        gt_names_3d = info["gt_names"][mask]
        gt_labels_3d = []
        for cat in gt_names_3d:
            if cat in self.CLASSES:
                gt_labels_3d.append(self.CLASSES.index(cat))
            else:
                gt_labels_3d.append(-1)
        gt_labels_3d = np.array(gt_labels_3d)

        if self.with_velocity:
            gt_velocity = info["gt_velocity"][mask]
            nan_mask = np.isnan(gt_velocity[:, 0])
            gt_velocity[nan_mask] = [0.0, 0.0]
            gt_bboxes_3d = np.concatenate([gt_bboxes_3d, gt_velocity], axis=-1)

        # the nuscenes box center is [0.5, 0.5, 0.5], we change it to be
        # the same as KITTI (0.5, 0.5, 0)
        # haotian: this is an important change: from 0.5, 0.5, 0.5 -> 0.5, 0.5, 0
        #! self.box_mode_3d: Box3DMode.LIDAR
        #! before conver, numpy.array, [N, 9]
        #!     origin: [ 6.31729126e+00 -7.27024364e+01 -5.54567343e-03  
        #!               4.19283009e+00  1.81618583e+00  1.47383118e+00  
        #!               1.56660185e+00  0.00000000e+00  0.00000000e+00]


        #! gt_bboxes_3d: 变换前
        # [[-7.43145523e+01  7.88936310e+01 -5.54647436e-03  4.19283009e+00  1.81618583e+00  1.47383118e+00 -1.57118204e+00  0.00000000e+00  0.00000000e+00]
        # [ 9.86259460e+00  7.89334259e+01  7.39851147e-02  4.61100578e+00  2.24171329e+00  1.66727591e+00 -1.57072123e+00  0.00000000e+00  0.00000000e+00]
        # [-7.68924026e+01  8.23912506e+01 -2.38877479e-02  4.97424412e+00  2.03840137e+00  1.55429590e+00 -1.57112775e+00  0.00000000e+00  0.00000000e+00]
        # [ 9.69602966e+00  6.84120483e+01 -5.87919215e-03  4.71752501e+00  1.89482689e+00  1.30093992e+00 -1.57113294e+00  0.00000000e+00  0.00000000e+00]
        # [-7.75763397e+01 -3.29122467e+01 -1.16683003e-02  4.18121004e+00  1.99411714e+00  1.38529611e+00 -1.57488842e+00  0.00000000e+00  0.00000000e+00]]

        #! 这行代码一点也不变
        gt_bboxes_3d = LiDARInstance3DBoxes(
            gt_bboxes_3d, box_dim=gt_bboxes_3d.shape[-1], origin=(0.5, 0.5, 0)
        ).convert_to(self.box_mode_3d)
        #! gt_bboxes_3d: 变换后
        # tensor([[-7.4315e+01,  7.8894e+01, -5.5465e-03,  4.1928e+00,  1.8162e+00,  1.4738e+00, -1.5712e+00,  0.0000e+00,  0.0000e+00],
        #         [ 9.8626e+00,  7.8933e+01,  7.3985e-02,  4.6110e+00,  2.2417e+00, 1.6673e+00, -1.5707e+00,  0.0000e+00,  0.0000e+00],
        #         [-7.6892e+01,  8.2391e+01, -2.3888e-02,  4.9742e+00,  2.0384e+00, 1.5543e+00, -1.5711e+00,  0.0000e+00,  0.0000e+00],
        #         [ 9.6960e+00,  6.8412e+01, -5.8792e-03,  4.7175e+00,  1.8948e+00, 1.3009e+00, -1.5711e+00,  0.0000e+00,  0.0000e+00],
        #         [-7.7576e+01, -3.2912e+01, -1.1668e-02,  4.1812e+00,  1.9941e+00, 1.3853e+00, -1.5749e+00,  0.0000e+00,  0.0000e+00]])

        #! after convert, <class 'mmdet3d.core.bbox.structures.lidar_box3d.LiDARInstance3DBoxes'>
        #! after: tensor([ 6.3173e+00, -7.2702e+01, -5.5457e-03,  
        #!                 4.1928e+00,  1.8162e+00,  1.4738e+00,  
        #!                 1.5666e+00,  0.0000e+00,  0.0000e+00]



        #! gt_lables_3d: (N, ) [0, 0, ..., 0]
        #! gt_names_3d: (N, )  ['car', 'car', ..., 'car]
        anns_results = dict(
            gt_bboxes_3d=gt_bboxes_3d, 
            gt_labels_3d=gt_labels_3d,
            gt_names=gt_names_3d,
        )
        return anns_results

    def _format_bbox(self, results, jsonfile_prefix=None):
        """Convert the results to the standard format.

        Args:
            results (list[dict]): Testing results of the dataset.
            jsonfile_prefix (str): The prefix of the output jsonfile.
                You can specify the output directory/filename by
                modifying the jsonfile_prefix. Default: None.

        Returns:
            str: Path of the output json file.
        """
        nusc_annos = {}
        mapped_class_names = self.CLASSES

        print("Start to convert detection format...")
        for sample_id, det in enumerate(mmcv.track_iter_progress(results)):
            annos = []
            boxes = output_to_nusc_box(det)
            sample_token = self.data_infos[sample_id]["token"]
            boxes = lidar_nusc_box_to_global(
                self.data_infos[sample_id],
                boxes,
                mapped_class_names,
                self.eval_detection_configs,
                self.eval_version,
            )
            for i, box in enumerate(boxes):
                # name = mapped_class_names[box.label]
                name = 'car'
                if np.sqrt(box.velocity[0] ** 2 + box.velocity[1] ** 2) > 0.2:
                    if name in [
                        "car",
                        "construction_vehicle",
                        "bus",
                        "truck",
                        "trailer",
                    ]:
                        attr = "vehicle.moving"
                    elif name in ["bicycle", "motorcycle"]:
                        attr = "cycle.with_rider"
                    else:
                        attr = NuScenesDataset.DefaultAttribute[name]
                else:
                    if name in ["pedestrian"]:
                        attr = "pedestrian.standing"
                    elif name in ["bus"]:
                        attr = "vehicle.stopped"
                    else:
                        attr = NuScenesDataset.DefaultAttribute[name]

                nusc_anno = dict(
                    sample_token=sample_token,
                    translation=box.center.tolist(),
                    size=box.wlh.tolist(),
                    rotation=box.orientation.elements.tolist(),
                    velocity=box.velocity[:2].tolist(),
                    detection_name=name,
                    detection_score=box.score,
                    attribute_name=attr,
                )
                annos.append(nusc_anno)
            nusc_annos[sample_token] = annos
        nusc_submissions = {
            "meta": self.modality,
            "results": nusc_annos,
        }

        mmcv.mkdir_or_exist(jsonfile_prefix)
        res_path = osp.join(jsonfile_prefix, "results_nusc.json")
        print("Results writes to", res_path)
        mmcv.dump(nusc_submissions, res_path)
        return res_path

    def _evaluate_single(
        self,
        result_path,
        logger=None,
        metric="bbox",
        result_name="pts_bbox",
    ):
        """Evaluation for a single model in nuScenes protocol.

        Args:
            result_path (str): Path of the result file.
            logger (logging.Logger | str | None): Logger used for printing
                related information during evaluation. Default: None.
            metric (str): Metric name used for evaluation. Default: 'bbox'.
            result_name (str): Result name in the metric prefix.
                Default: 'pts_bbox'.

        Returns:
            dict: Dictionary of evaluation details.
        """
        from nuscenes import NuScenes
        #from nuscenes.eval.detection.evaluate import DetectionEval
        from projects.evaluate import DetectionEval

        output_dir = osp.join(*osp.split(result_path)[:-1])
        nusc = NuScenes(version=self.version, dataroot=self.dataset_root, verbose=False)
        eval_set_map = {
            "v1.0-mini": "mini_val",
            "v1.0-trainval": "val",
            "v1.0-test": "test",
        }
        nusc_eval = DetectionEval(
            nusc,
            config=self.eval_detection_configs,
            result_path=result_path,
            eval_set=eval_set_map[self.version],
            output_dir=output_dir,
            verbose=False,
        )

        # batch_box_preds, batch_cls_preds, anchors = format_before_nms(nusc_eval)

        # code_type = 'faf'
        # config = MyConfig(motion_state = False, pred_type = 'motion')
        # batch_motion_preds = None
        # class_selected = apply_nms_det(
        #     batch_box_preds,
        #     batch_cls_preds,
        #     anchors,
        #     code_type,
        #     config,
        #     batch_motion_preds,
        # )
        #pred_results, gt_results = format_results_iou(nusc_eval)

        #pred_selected = []
        # for selected, result in zip(class_selected[0], pred_results):
        #     selected_idx = selected[0]['selected_idx']
        #     selected_result = result[0][selected_idx, :]
        #     pred_selected.append([selected_result])
        # pred_results = pred_selected

        #mean_ap, _ = eval_map(
        #    pred_results,
        #    gt_results,
        #    scale_ranges=None,
        #    iou_thr=0.5,
        #    dataset=None,
        #    logger=None,
        #)
        #print('mean_ap = ', mean_ap)
        
        #mean_ap, _ = eval_map(
        #    pred_results,
        #    gt_results,
        #    scale_ranges=None,
        #    iou_thr=0.7,
        #    dataset=None,
        #    logger=None,
        #)
        #print('mean_ap = ', mean_ap)

        nusc_eval.main(render_curves=False)

        # record metrics
        metrics = mmcv.load(osp.join(output_dir, "metrics_summary.json"))
        detail = dict()
        for name in self.CLASSES:
            for k, v in metrics["label_aps"][name].items():
                val = float("{:.4f}".format(v))
                detail["object/{}_ap_dist_{}".format(name, k)] = val
            for k, v in metrics["label_tp_errors"][name].items():
                val = float("{:.4f}".format(v))
                detail["object/{}_{}".format(name, k)] = val
            for k, v in metrics["tp_errors"].items():
                val = float("{:.4f}".format(v))
                detail["object/{}".format(self.ErrNameMapping[k])] = val

        detail["object/nds"] = metrics["nd_score"]
        detail["object/map"] = metrics["mean_ap"]
        return detail

    def format_results(self, results, jsonfile_prefix=None):
        """Format the results to json (standard format for COCO evaluation).

        Args:
            results (list[dict]): Testing results of the dataset.
            jsonfile_prefix (str | None): The prefix of json files. It includes
                the file path and the prefix of filename, e.g., "a/b/prefix".
                If not specified, a temp file will be created. Default: None.

        Returns:
            tuple: Returns (result_files, tmp_dir), where `result_files` is a \
                dict containing the json filepaths, `tmp_dir` is the temporal \
                directory created for saving json files when \
                `jsonfile_prefix` is not specified.
        """
        assert isinstance(results, list), "results must be a list"
        assert len(results) == len(
            self
        ), "The length of results is not equal to the dataset len: {} != {}".format(
            len(results), len(self)
        )

        if jsonfile_prefix is None:
            tmp_dir = tempfile.TemporaryDirectory()
            jsonfile_prefix = osp.join(tmp_dir.name, "results")
        else:
            tmp_dir = None

        result_files = self._format_bbox(results, jsonfile_prefix)
        return result_files, tmp_dir

    def evaluate_map(self, results):
        thresholds = torch.tensor([0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65])

        num_classes = len(self.map_classes)
        num_thresholds = len(thresholds)

        tp = torch.zeros(num_classes, num_thresholds)
        fp = torch.zeros(num_classes, num_thresholds)
        fn = torch.zeros(num_classes, num_thresholds)

        for result in results:
            pred = result["masks_bev"]
            label = result["gt_masks_bev"]

            pred = pred.detach().reshape(num_classes, -1)
            label = label.detach().bool().reshape(num_classes, -1)

            pred = pred[:, :, None] >= thresholds
            label = label[:, :, None]

            tp += (pred & label).sum(dim=1)
            fp += (pred & ~label).sum(dim=1)
            fn += (~pred & label).sum(dim=1)

        ious = tp / (tp + fp + fn + 1e-7)

        metrics = {}
        for index, name in enumerate(self.map_classes):
            metrics[f"map/{name}/iou@max"] = ious[index].max().item()
            for threshold, iou in zip(thresholds, ious[index]):
                metrics[f"map/{name}/iou@{threshold.item():.2f}"] = iou.item()
        metrics["map/mean/iou@max"] = ious.max(dim=1).values.mean().item()
        return metrics

    def evaluate(
        self,
        results,
        metric="bbox",
        jsonfile_prefix=None,
        result_names=["pts_bbox"],
        **kwargs,
    ):
        """Evaluation in nuScenes protocol.

        Args:
            results (list[dict]): Testing results of the dataset.
            metric (str | list[str]): Metrics to be evaluated.
            jsonfile_prefix (str | None): The prefix of json files. It includes
                the file path and the prefix of filename, e.g., "a/b/prefix".
                If not specified, a temp file will be created. Default: None.

        Returns:
            dict[str, float]: Results of each evaluation metric.
        """

        metrics = {}

        if "masks_bev" in results[0]:
            metrics.update(self.evaluate_map(results))

        if "boxes_3d" in results[0]:
            result_files, tmp_dir = self.format_results(results, jsonfile_prefix)

            if isinstance(result_files, dict):
                for name in result_names:
                    print("Evaluating bboxes of {}".format(name))
                    ret_dict = self._evaluate_single(result_files[name])
                metrics.update(ret_dict)
            elif isinstance(result_files, str):
                metrics.update(self._evaluate_single(result_files))

            if tmp_dir is not None:
                tmp_dir.cleanup()

        return metrics


def output_to_nusc_box(detection):
    """Convert the output to the box class in the nuScenes.

    Args:
        detection (dict): Detection results.

            - boxes_3d (:obj:`BaseInstance3DBoxes`): Detection bbox.
            - scores_3d (torch.Tensor): Detection scores.
            - labels_3d (torch.Tensor): Predicted box labels.

    Returns:
        list[:obj:`NuScenesBox`]: List of standard NuScenesBoxes.
    """
    box3d = detection["boxes_3d"]
    scores = detection["scores_3d"].numpy()
    labels = detection["labels_3d"].numpy()

    box_gravity_center = box3d.gravity_center.numpy()
    box_dims = box3d.dims.numpy()
    box_yaw = box3d.yaw.numpy()
    # TODO: check whether this is necessary
    # with dir_offset & dir_limit in the head
    box_yaw = -box_yaw - np.pi / 2

    box_list = []
    for i in range(len(box3d)):
        quat = pyquaternion.Quaternion(axis=[0, 0, 1], radians=box_yaw[i])
        velocity = (*box3d.tensor[i, 7:9], 0.0)
        # velo_val = np.linalg.norm(box3d[i, 7:9])
        # velo_ori = box3d[i, 6]
        # velocity = (
        # velo_val * np.cos(velo_ori), velo_val * np.sin(velo_ori), 0.0)
        box = NuScenesBox(
            box_gravity_center[i],
            box_dims[i],
            quat,
            label=labels[i],
            score=scores[i],
            velocity=velocity,
        )
        box_list.append(box)
    return box_list


def lidar_nusc_box_to_global(
    info, boxes, classes, eval_configs, eval_version="detection_cvpr_2019"
):
    """Convert the box from ego to global coordinate.

    Args:
        info (dict): Info for a specific sample data, including the
            calibration information.
        boxes (list[:obj:`NuScenesBox`]): List of predicted NuScenesBoxes.
        classes (list[str]): Mapped classes in the evaluation.
        eval_configs : Evaluation configuration object.
        eval_version (str): Evaluation version.
            Default: 'detection_cvpr_2019'

    Returns:
        list: List of standard NuScenesBoxes in the global
            coordinate.
    """
    box_list = []
    for box in boxes:
        # Move box to ego vehicle coord system
        box.rotate(pyquaternion.Quaternion(info["lidar2ego_rotation"]))
        box.translate(np.array(info["lidar2ego_translation"]))

        # filter det in ego.
        cls_range_map = eval_configs.class_range
        radius = np.linalg.norm(box.center[:2], 2)
        # det_range = cls_range_map[classes[box.label]]
        det_range = 150
        if radius > det_range:
            continue
        # Move box to global coord system
        box.rotate(pyquaternion.Quaternion(info["ego2global_rotation"]))
        box.translate(np.array(info["ego2global_translation"]))
        box_list.append(box)
    return box_list


# https://github.com/carla-simulator/carla/blob/master/PythonAPI/examples/client_bounding_boxes.py
def get_matrix(pitch=0, yaw=0, roll=0):
    """
    Creates matrix from carla transform.
    """

    # rotation = transform.rotation
    # location = transform.location
    c_y = np.cos(np.radians(yaw))
    s_y = np.sin(np.radians(yaw))
    c_r = np.cos(np.radians(roll))
    s_r = np.sin(np.radians(roll))
    c_p = np.cos(np.radians(pitch))
    s_p = np.sin(np.radians(pitch))
    # matrix = np.matrix(np.identity(3))
    matrix = np.identity(3)
    # matrix[0, 3] = location.x
    # matrix[1, 3] = location.y
    # matrix[2, 3] = location.z
    matrix[0, 0] = c_p * c_y
    matrix[0, 1] = c_y * s_p * s_r - s_y * c_r
    matrix[0, 2] = -c_y * s_p * c_r - s_y * s_r
    matrix[1, 0] = s_y * c_p
    matrix[1, 1] = s_y * s_p * s_r + c_y * c_r
    matrix[1, 2] = -s_y * s_p * c_r + c_y * s_r
    matrix[2, 0] = s_p
    matrix[2, 1] = -c_p * s_r
    matrix[2, 2] = c_p * c_r
    return matrix



def format_results_iou(nusc_eval):
    """
    Convert the preditions and GT for IoU metric.
    """

    gt_boxes = nusc_eval.gt_boxes.boxes
    gt_boxes_list = [ ]

    key_list = [ ]
    for key, value in gt_boxes.items():
        box_list = [ ]
        box_dic = { }
        key_list.append(key)
        for Det_box in value:
            translation = Det_box.translation
            size = Det_box.size
            x0 = translation[0] - size[0] / 2
            x1 = translation[0] + size[0] / 2
            y0 = translation[1] - size[1] / 2
            y1 = translation[1] + size[1] / 2
            coordinate_box = [x0, y0, x0, y1, x1, y1, x1, y0]
            box_list.append(coordinate_box)
        box_array = np.array(box_list)
        labels = np.zeros(len(box_list), dtype=int)
        box_dic['bboxes'] = box_array
        box_dic['labels'] = labels
        gt_boxes_list.append(box_dic)

    pred_boxes = nusc_eval.pred_boxes.boxes
    pred_boxes_list = [ ]
    # for key, value in pred_boxes.items():
    for key in key_list:
        value = pred_boxes[key]
        box_list = [ ]
        for Det_box in value:
            translation = Det_box.translation
            size = Det_box.size
            x0 = translation[0] - size[0] / 2
            x1 = translation[0] + size[0] / 2
            y0 = translation[1] - size[1] / 2
            y1 = translation[1] + size[1] / 2
            score = Det_box.detection_score
            coordinate_box = [x0, y0, x0, y1, x1, y1, x1, y0, score]
            box_list.append(coordinate_box)
        box_array = np.array(box_list)
        pred_boxes_list.append([box_array])

    return pred_boxes_list, gt_boxes_list

import math
def format_before_nms(nusc_eval):
    """
    Convert the preditions and GT before the NMS operation.
    """

    gt_boxes = nusc_eval.gt_boxes.boxes
    gt_boxes_list = [ ]
    for key, value in gt_boxes.items():
        box_list = [ ]
        # box_dic = { }
        for Det_box in value:
            translation = Det_box.translation
            size = Det_box.size
            x, y = translation[0], translation[1]
            w, h = size[0], size[1]
            # carla
            angles_1 = quat2euler(Det_box.rotation)
            # angles_1 = [angle * 180 / (np.pi) for angle in angles_1]
            # (roll, pitch, yaw) ---> (pitch, yaw, roll)
            # camera2ego[:3, :3] = get_matrix(pitch=angles_1[1], yaw=angles_1[2], roll=angles_1[0])
            yaw = angles_1[2]
            rot_sin, rot_cos = math.sin(yaw), math.cos(yaw)
            box_code = [x, y, w, h, rot_sin, rot_cos]
            box_list.append(box_code)
        box_array = np.array(box_list)
        box_array = torch.from_numpy(box_array)
        gt_boxes_list.append(box_array)
    batch_anchors = gt_boxes_list


    pred_boxes = nusc_eval.pred_boxes.boxes
    pred_boxes_list = [ ]
    pred_score_list = [ ]
    for key, value in pred_boxes.items():
        box_list = [ ]
        score_list = [ ]
        for Det_box in value:
            translation = Det_box.translation
            size = Det_box.size
            x, y = translation[0], translation[1]
            w, h = size[0], size[1]

            # carla
            angles_1 = quat2euler(Det_box.rotation)
            # angles_1 = [angle * 180 / (np.pi) for angle in angles_1]
            # (roll, pitch, yaw) ---> (pitch, yaw, roll)
            # camera2ego[:3, :3] = get_matrix(pitch=angles_1[1], yaw=angles_1[2], roll=angles_1[0])
            yaw = angles_1[2]
            rot_sin, rot_cos = math.sin(yaw), math.cos(yaw)
            box_code = [x, y, w, h, rot_sin, rot_cos]
            box_list.append(box_code)

            score = Det_box.detection_score
            score_code = [1 - score , score]
            score_list.append(score_code)
        box_array = np.array(box_list)
        pred_boxes_list.append(box_array)
        score_array = np.array(score_list)
        pred_score_list.append(score_array)
    batch_box_preds = np.array(pred_boxes_list)
    batch_cls_preds = np.array(pred_score_list)

    batch_box_preds = torch.from_numpy(batch_box_preds)
    batch_cls_preds = torch.from_numpy(batch_cls_preds)


    return batch_box_preds, batch_cls_preds, batch_anchors
