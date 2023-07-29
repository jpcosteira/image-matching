import torch
import numpy as np
import cv2
import gradio as gr
from hloc import matchers, extractors
from hloc.utils.base_model import dynamic_load
from hloc import match_dense, match_features, extract_features
from .viz import draw_matches, fig2im, plot_images, plot_color_line_matches

device = "cuda" if torch.cuda.is_available() else "cpu"


def get_model(match_conf):
    Model = dynamic_load(matchers, match_conf["model"]["name"])
    model = Model(match_conf["model"]).eval().to(device)
    return model


def get_feature_model(conf):
    Model = dynamic_load(extractors, conf["model"]["name"])
    model = Model(conf["model"]).eval().to(device)
    return model


def filter_matches(pred, reproj_threshold=4.0):
    mkpts0 = None
    mkpts1 = None
    if "keypoints0_orig" in pred.keys() and "keypoints1_orig" in pred.keys():
        mkpts0 = pred["keypoints0_orig"]
        mkpts1 = pred["keypoints1_orig"]

    if (
        "line_keypoints0_orig" in pred.keys()
        and "line_keypoints1_orig" in pred.keys()
    ):
        mkpts0 = pred["line_keypoints0_orig"]
        mkpts1 = pred["line_keypoints1_orig"]

    H, mask = cv2.findHomography(
        mkpts0,
        mkpts1,
        method=cv2.USAC_MAGSAC,
        ransacReprojThreshold=8.0,
        confidence=0.9999,
        maxIters=10000,
    )
    mask = np.array(mask.ravel().astype("bool"), dtype="bool")
    if H is not None:
        pred["keypoints0_orig"] = pred["keypoints0_orig"][mask]
        pred["keypoints1_orig"] = pred["keypoints1_orig"][mask]
        pred["mconf"] = pred["mconf"][mask]
    return pred


def compute_geom(pred) -> dict:
    mkpts0 = None
    mkpts1 = None

    if "keypoints0_orig" in pred.keys() and "keypoints1_orig" in pred.keys():
        mkpts0 = pred["keypoints0_orig"]
        mkpts1 = pred["keypoints1_orig"]

    if (
        "line_keypoints0_orig" in pred.keys()
        and "line_keypoints1_orig" in pred.keys()
    ):
        mkpts0 = pred["line_keypoints0_orig"]
        mkpts1 = pred["line_keypoints1_orig"]

    if mkpts0 is not None and mkpts1 is not None:
        if len(mkpts0) < 8:
            return {}
        h1, w1, _ = pred["image0_orig"].shape
        geo_info = {}
        F, inliers = cv2.findFundamentalMat(
            mkpts0,
            mkpts1,
            method=cv2.USAC_MAGSAC,
            ransacReprojThreshold=1.0,
            confidence=0.9999,
            maxIters=10000,
        )
        geo_info["Fundamental"] = F.tolist()
        H, _ = cv2.findHomography(
            mkpts1,
            mkpts0,
            method=cv2.USAC_MAGSAC,
            ransacReprojThreshold=5.0,
            confidence=0.9999,
            maxIters=10000,
        )
        geo_info["Homography"] = H.tolist()
        _, H1, H2 = cv2.stereoRectifyUncalibrated(
            mkpts0.reshape(-1, 2), mkpts1.reshape(-1, 2), F, imgSize=(w1, h1)
        )
        geo_info["H1"] = H1.tolist()
        geo_info["H2"] = H2.tolist()
        return geo_info
    else:
        return {}


def wrap_images(img0, img1, geo_info, geom_type):
    h1, w1, _ = img0.shape
    h2, w2, _ = img1.shape
    result_matrix = None
    if geo_info is not None and len(geo_info) != 0:
        rectified_image0 = img0
        rectified_image1 = None
        H = np.array(geo_info["Homography"])
        F = np.array(geo_info["Fundamental"])
        title = []
        if geom_type == "Homography":
            rectified_image1 = cv2.warpPerspective(
                img1, H, (img0.shape[1] + img1.shape[1], img0.shape[0])
            )
            result_matrix = H
            title = ["Image 0", "Image 1 - warped"]
        elif geom_type == "Fundamental":
            H1, H2 = np.array(geo_info["H1"]), np.array(geo_info["H2"])
            rectified_image0 = cv2.warpPerspective(img0, H1, (w1, h1))
            rectified_image1 = cv2.warpPerspective(img1, H2, (w2, h2))
            result_matrix = F
            title = ["Image 0 - warped", "Image 1 - warped"]
        else:
            print("Error: Unknown geometry type")
        fig = plot_images(
            [rectified_image0.squeeze(), rectified_image1.squeeze()],
            title,
            dpi=300,
        )
        dictionary = {
            "row1": result_matrix[0].tolist(),
            "row2": result_matrix[1].tolist(),
            "row3": result_matrix[2].tolist(),
        }
        return fig2im(fig), dictionary
    else:
        return None, None


def change_estimate_geom(input_image0, input_image1, matches_info, choice):
    if (
        matches_info is None
        or len(matches_info) < 1
        or "geom_info" not in matches_info.keys()
    ):
        return None, None
    geom_info = matches_info["geom_info"]
    wrapped_images = None
    if choice != "No":
        wrapped_images, _ = wrap_images(
            input_image0, input_image1, geom_info, choice
        )
        return wrapped_images, matches_info
    else:
        return None, None


def display_matches(pred: dict):
    img0 = pred["image0_orig"]
    img1 = pred["image1_orig"]

    num_inliers = 0
    if "keypoints0_orig" in pred.keys() and "keypoints1_orig" in pred.keys():
        mkpts0 = pred["keypoints0_orig"]
        mkpts1 = pred["keypoints1_orig"]
        num_inliers = len(mkpts0)
        if "mconf" in pred.keys():
            mconf = pred["mconf"]
        else:
            mconf = np.ones(len(mkpts0))
        fig_mkpts = draw_matches(
            mkpts0,
            mkpts1,
            img0,
            img1,
            mconf,
            dpi=300,
            titles=[
                "Image 0 - matched keypoints",
                "Image 1 - matched keypoints",
            ],
        )
        fig = fig_mkpts
    if "line0_orig" in pred.keys() and "line1_orig" in pred.keys():
        # lines
        mtlines0 = pred["line0_orig"]
        mtlines1 = pred["line1_orig"]
        num_inliers = len(mtlines0)
        fig_lines = plot_images(
            [img0.squeeze(), img1.squeeze()],
            ["Image 0 - matched lines", "Image 1 - matched lines"],
            dpi=300,
        )
        fig_lines = plot_color_line_matches([mtlines0, mtlines1], lw=2)
        fig_lines = fig2im(fig_lines)

        # keypoints
        mkpts0 = pred["line_keypoints0_orig"]
        mkpts1 = pred["line_keypoints1_orig"]

        if mkpts0 is not None and mkpts1 is not None:
            num_inliers = len(mkpts0)
            if "mconf" in pred.keys():
                mconf = pred["mconf"]
            else:
                mconf = np.ones(len(mkpts0))
            fig_mkpts = draw_matches(mkpts0, mkpts1, img0, img1, mconf, dpi=300)
            fig_lines = cv2.resize(
                fig_lines, (fig_mkpts.shape[1], fig_mkpts.shape[0])
            )
            fig = np.concatenate([fig_mkpts, fig_lines], axis=0)
        else:
            fig = fig_lines
    return fig, num_inliers


def run_matching(
    image0,
    image1,
    match_threshold,
    extract_max_keypoints,
    keypoint_threshold,
    enable_ransac,
    key,
):
    # image0 and image1 is RGB mode
    if image0 is None or image1 is None:
        raise gr.Error("Error: No images found! Please upload two images.")

    model = matcher_zoo[key]
    match_conf = model["config"]
    # update match config
    match_conf["model"]["match_threshold"] = match_threshold
    match_conf["model"]["max_keypoints"] = extract_max_keypoints

    matcher = get_model(match_conf)
    if model["dense"]:
        pred = match_dense.match_images(
            matcher, image0, image1, match_conf["preprocessing"], device=device
        )
        del matcher
        extract_conf = None
    else:
        extract_conf = model["config_feature"]
        # update extract config
        extract_conf["model"]["max_keypoints"] = extract_max_keypoints
        extract_conf["model"]["keypoint_threshold"] = keypoint_threshold
        extractor = get_feature_model(extract_conf)
        pred0 = extract_features.extract(
            extractor, image0, extract_conf["preprocessing"]
        )
        pred1 = extract_features.extract(
            extractor, image1, extract_conf["preprocessing"]
        )
        pred = match_features.match_images(matcher, pred0, pred1)
        del extractor

    if enable_ransac:
        filter_matches(pred, reproj_threshold=4.0)

    fig, num_inliers = display_matches(pred)
    geom_info = compute_geom(pred)
    output_wrapped, _ = change_estimate_geom(
        pred["image0_orig"],
        pred["image1_orig"],
        {"geom_info": geom_info},
        "Homography",
    )
    del pred
    return (
        fig,
        {"matches number": num_inliers},
        {
            "match_conf": match_conf,
            "extractor_conf": extract_conf,
        },
        {
            "geom_info": geom_info,
        },
        output_wrapped,
        # geometry_result,
    )


# Matchers collections
matcher_zoo = {
    "gluestick": {"config": match_dense.confs["gluestick"], "dense": True},
    "sold2": {"config": match_dense.confs["sold2"], "dense": True},
    # 'dedode-sparse': {
    #     'config': match_dense.confs['dedode_sparse'],
    #     'dense': True  # dense mode, we need 2 images
    # },
    "loftr": {"config": match_dense.confs["loftr"], "dense": True},
    "topicfm": {"config": match_dense.confs["topicfm"], "dense": True},
    "aspanformer": {"config": match_dense.confs["aspanformer"], "dense": True},
    "dedode": {
        "config": match_features.confs["Dual-Softmax"],
        "config_feature": extract_features.confs["dedode"],
        "dense": False,
    },
    "superpoint+superglue": {
        "config": match_features.confs["superglue"],
        "config_feature": extract_features.confs["superpoint_max"],
        "dense": False,
    },
    "superpoint+lightglue": {
        "config": match_features.confs["superpoint-lightglue"],
        "config_feature": extract_features.confs["superpoint_max"],
        "dense": False,
    },
    "disk": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["disk"],
        "dense": False,
    },
    "disk+dualsoftmax": {
        "config": match_features.confs["Dual-Softmax"],
        "config_feature": extract_features.confs["disk"],
        "dense": False,
    },
    "superpoint+dualsoftmax": {
        "config": match_features.confs["Dual-Softmax"],
        "config_feature": extract_features.confs["superpoint_max"],
        "dense": False,
    },
    "disk+lightglue": {
        "config": match_features.confs["disk-lightglue"],
        "config_feature": extract_features.confs["disk"],
        "dense": False,
    },
    "superpoint+mnn": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["superpoint_max"],
        "dense": False,
    },
    "sift+sgmnet": {
        "config": match_features.confs["sgmnet"],
        "config_feature": extract_features.confs["sift"],
        "dense": False,
    },
    "sosnet": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["sosnet"],
        "dense": False,
    },
    "hardnet": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["hardnet"],
        "dense": False,
    },
    "d2net": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["d2net-ss"],
        "dense": False,
    },
    "d2net-ms": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["d2net-ms"],
        "dense": False,
    },
    "alike": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["alike"],
        "dense": False,
    },
    "lanet": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["lanet"],
        "dense": False,
    },
    "r2d2": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["r2d2"],
        "dense": False,
    },
    "darkfeat": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["darkfeat"],
        "dense": False,
    },
    "sift": {
        "config": match_features.confs["NN-mutual"],
        "config_feature": extract_features.confs["sift"],
        "dense": False,
    },
    "roma": {"config": match_dense.confs["roma"], "dense": True},
    "DKMv3": {"config": match_dense.confs["dkm"], "dense": True},
}