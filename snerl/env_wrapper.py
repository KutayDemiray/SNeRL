import gymnasium as gym
import metaworld
import numpy as np
import random
import torch

from typing import List
from datetime import datetime

LONGITUDE = 20
LATITUDE = 5


# kutay
def get_cam_poses(env, ids=None):
    """Returns camera pose matrices (4x4) for each camera"""
    # in these environments, first 3 and last 2 cameras are built in
    # this means the range [4:-2] contains our cameras

    if ids is None:
        # get all cameras
        cam_positions = env.mujoco_renderer.data.cam_xpos[4:-2]
        cam_rotations = env.mujoco_renderer.data.cam_xmat[4:-2]
    else:
        cam_positions = env.mujoco_renderer.data.cam_xpos[ids]
        cam_rotations = env.mujoco_renderer.data.cam_xmat[ids]

    n_cams = cam_positions.shape[0]
    ret = np.zeros((n_cams, 4, 4))
    for i in range(n_cams):
        rot = cam_rotations[i].reshape((3, 3))
        pos = cam_positions[i]
        ret[i, 0:3, 0:3] = rot
        ret[i, 0:3, 3] = pos.T
        ret[i, 3, 3] = 1

    return ret, cam_positions, cam_rotations


# kutay
def cam_name_to_cam_id(cam_name: str):
    """Returns camera id from name (only for "CUSTOM" cameras)"""
    # in these environments, first 3 and last 2 cameras are built in
    # this means the range [4:-2] contains our cameras
    return 4 + int(cam_name[6:])  # 4 + number at the end of camera name


class EnvWrapper(object):
    def __init__(
        self,
        env_name,
        from_pixels,
        height,
        width,
        frame_skip,
        camera_name,
        multicam_contrastive,
        sparse_reward,
        device,
        snerl_cam_names: List[str],
        tsne=False,
        render_mode="rgbd_array",
    ):
        assert from_pixels
        ml1 = metaworld.ML1(env_name)  # Construct the benchmark, sampling tasks
        self.env = ml1.train_classes[env_name](
            render_mode=render_mode  # kutay
        )  # Create an environment with task `pick_place`
        self.env.set_task(ml1.train_tasks[0])
        self.device = device
        print("env device", self.device)
        self.tsne = tsne
        self.snerl_cam_names = snerl_cam_names
        self.render_mode = render_mode

        if env_name == "window-open-v2":
            raise Exception("window-open-v2 is work in progress")
            self.env.random_init = False
            # set camera pos
            for i in range(LONGITUDE):
                for j in range(LATITUDE):
                    cam_name_repose = "cam_%d_%d" % (i, j)
                    body_ids = self.env.model.camera_name2id(cam_name_repose)
                    self.env.model.cam_pos[body_ids] = (
                        self.env.obj_init_pos + self.env.model.cam_pos[body_ids]
                    )
            for i in range(LONGITUDE * 10):
                for j in range(LATITUDE * 10):
                    cam_name_repose = "cam2_%d_%d" % (i, j)
                    body_ids = self.env.model.camera_name2id(cam_name_repose)
                    self.env.model.cam_pos[body_ids] = (
                        self.env.obj_init_pos + self.env.model.cam_pos[body_ids]
                    )
        else:
            self.env.random_init = True
            self.default_campos = np.zeros(
                (len(self.snerl_cam_names), 3)
            )  # n_cameras * positions

            cam_ids = [
                cam_name_to_cam_id(cam_name) for cam_name in self.snerl_cam_names
            ]
            cam_poses, cam_positions, cam_rotations = get_cam_poses(
                env=self.env, ids=cam_ids
            )

            for i in range(len(self.snerl_cam_names)):  # n_cams
                self.default_campos[i] = cam_positions[i]

            """
            body_ids = self.env.model.camera_name2id("cam_1_1")
            self.default_campos[0] = self.env.model.cam_pos[body_ids]
            body_ids = self.env.model.camera_name2id("cam_7_4")
            self.default_campos[1] = self.env.model.cam_pos[body_ids]
            body_ids = self.env.model.camera_name2id("cam_14_2")
            self.default_campos[2] = self.env.model.cam_pos[body_ids]
            """
        self.env_name = env_name
        self.from_pixels = from_pixels
        self.height = height
        self.width = width
        self.sparse_reward = sparse_reward
        self._max_episode_steps = self.env.max_path_length
        self.multicam_contrastive = multicam_contrastive

        self.camera_name = camera_name
        self.multicam_contrastive = multicam_contrastive

    def background_mask(self, single_image, single_depth):
        single_image = torch.from_numpy(single_image).to(self.device)
        single_depth = torch.from_numpy(single_depth).to(self.device)
        mask = torch.zeros(single_depth.shape).to(self.device)
        mask[(single_depth) < 0.999] = 1
        mask = torch.unsqueeze(mask, -1)
        single_image = single_image * mask + 255 * (1 - mask)

        single_image = torch.permute(single_image, (2, 0, 1)).type(torch.uint8)
        return single_image

    # kutay
    def one_hot_affordance(
        self, obs: np.ndarray, color: np.ndarray = np.array([156, 104, 125])
    ) -> torch.Tensor:
        indices = np.where(np.all(obs == color, axis=-1))
        affordance = np.zeros((obs.shape[0], obs.shape[1], 1))
        affordance[indices] = 1

        affordance = torch.tensor(affordance).permute(2, 0, 1).to(self.device)
        return affordance

    def reset(self, *args, **kwargs):
        state_obs = self.env.reset()

        multicam_image = []
        if self.env_name == "window-open-v2":
            pass
        else:
            # body_ids = self.env.model.camera_name2id("cam_1_1")

            for i in range(len(self.snerl_cam_names)):
                body_id = cam_name_to_cam_id(self.snerl_cam_names[i])
                self.env.model.cam_pos[body_id] = (
                    self.env._target_pos[:3] + self.default_campos[i]
                )

            """
            body_ids = self.env.model.camera_name2id("cam_7_4")
            self.env.model.cam_pos[body_ids] = (
                self.env._target_pos[:3] + self.default_campos[1]
            )
            body_ids = self.env.model.camera_name2id("cam_14_2")
            self.env.model.cam_pos[body_ids] = (
                self.env._target_pos[:3] + self.default_campos[2]
            )
            """

        camera_name_aug = self.camera_name.copy()
        if self.multicam_contrastive:
            raise Exception("Work in progress")
            for single_cam in self.camera_name:
                a, b, c = single_cam.split("_")
                while True:
                    perturb_phi = random.randint(-10, 10)
                    perturb_psi = random.randint(-4, 4)
                    if perturb_phi != 0 or perturb_psi != 0:
                        break
                camera_name_aug.append(
                    "cam2_%d_%d" % (int(b) + perturb_phi, int(c) + perturb_psi)
                )

        for single_cam in camera_name_aug:
            """
            # this was the original code in snerl
            single_image, single_depth = self.sim.render(
                width=self.width, height=self.height, camera_name=single_cam, depth=True
            )
            """
            # kutay
            # print(single_cam)
            if self.render_mode == "rgbd_array":
                rgbd = self.env.render()
                single_image, single_depth = rgbd[:, :, :3], rgbd[:, :, 3]
            elif self.render_mode == "rgb_array":
                single_image, seg, single_depth = self.env.render()
                single_seg = self.one_hot_affordance(seg)
                # print(single_image.device, single_seg.device)

                # print(rgbd.shape)

            single_image = self.background_mask(single_image, single_depth)

            if self.render_mode == "rgbd_array":
                multicam_image.append(single_image)
            elif self.render_mode == "rgb_array":
                # print(single_image.device, single_seg.device)
                multicam_image.append(
                    torch.concatenate([single_image, single_seg], dim=0)
                )
            # kutay end

        if self.tsne:
            return torch.cat(multicam_image, dim=0), state_obs
        else:
            return torch.cat(multicam_image, dim=0)

    def step(self, action):
        state_obs, reward, terminated, truncated, info = self.env.step(action)
        done = terminated or truncated
        multicam_image = []

        camera_name_aug = self.camera_name.copy()
        if self.multicam_contrastive:
            for single_cam in self.camera_name:
                # print("first", single_cam)
                a, b, c = single_cam.split("_")
                while True:
                    perturb_phi = random.randint(-10, 10)
                    perturb_psi = random.randint(-4, 4)
                    if perturb_phi != 0 or perturb_psi != 0:
                        break
                camera_name_aug.append(
                    "cam2_%d_%d" % (int(b) + perturb_phi, int(c) + perturb_psi)
                )
        # print("aug", camera_name_aug)

        for single_cam in camera_name_aug:
            # kutay
            # print(single_cam)
            self.env.camera_name = single_cam
            if self.render_mode == "rgbd_array":
                rgbd = self.env.render()
                single_image, single_depth = rgbd[:, :, :3], rgbd[:, :, 3]
            elif self.render_mode == "rgb_array":
                single_image, seg, single_depth = self.env.render()
                single_seg = self.one_hot_affordance(seg)

                # print(rgbd.shape)

            single_image = self.background_mask(single_image, single_depth)

            if self.render_mode == "rgbd_array":
                multicam_image.append(single_image)
            elif self.render_mode == "rgb_array":
                multicam_image.append(
                    torch.concatenate([single_image, single_seg[None, ...]], dim=0)
                )
            # kutay end

        # print(len(multicam_image))
        if self.sparse_reward:
            reward = info["success"] - 1.0

        if self.env.curr_path_length == self._max_episode_steps:
            done = True

        """
        ts = datetime.timestamp(datetime.now())
        ts = datetime.fromtimestamp(ts)
        print(f"[{ts}] step end")
        """

        if self.tsne:
            ret = torch.cat(multicam_image, dim=0), reward, done, info, state_obs
            return ret
        else:
            ret = (
                torch.cat(multicam_image, dim=0),
                reward,
                done,
                info,
            )
            # print(ret[0].shape) 9x480x480

            return ret

    def __getattr__(self, attrname):
        return getattr(self.env, attrname)
