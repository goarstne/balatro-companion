"""Shared geometry for local recognition.

normalize(path_or_image) crops the game canvas out of a window capture (removes the black
letterbox bars of the stream) and scales it to REF_W wide, keeping the aspect ratio.
All region coordinates in the recognisers are fractions (x0, y0, x1, y1) of that canvas.
"""
from pathlib import Path

import cv2
import numpy as np

REF_W = 1280
ASSETS = Path.home() / ".local/share/balatro/assets"
DATASET = Path.home() / ".local/share/balatro/dataset"


def load(path_or_image):
    if isinstance(path_or_image, np.ndarray):
        return path_or_image
    img = cv2.imread(str(path_or_image), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"image not readable: {path_or_image}")
    return img


def canvas_box(img):
    """(r0, r1, c0, c1): the game canvas inside a window capture, in capture pixels."""
    mask = img.max(axis=2) > 30  # the letterbox bars are (near) black
    # longest non-black run: stray grey border lines outside the bars must not widen the crop
    r0, r1 = _longest_run(mask.mean(axis=1) > 0.2)
    # columns only within the canvas rows: averaged with the black bars, a dark game background (Celestial
    # pack starfield) fell below the threshold and cut the canvas short on the right
    # pack starfield (measured) has >= 17 % lit pixels per column, the bars <= 0.1 %
    return r0, r1, *_longest_run(mask[r0:r1].mean(axis=0) > 0.02)


def normalize(path_or_image):
    """BGR image of the game canvas, REF_W pixels wide."""
    img = load(path_or_image)
    r0, r1, c0, c1 = canvas_box(img)
    img = img[r0:r1, c0:c1]
    h, w = img.shape[:2]
    return cv2.resize(img, (REF_W, round(h * REF_W / w)), interpolation=cv2.INTER_AREA)


def _longest_run(ok):
    best, start = (0, len(ok)), None
    found = False
    for i, v in enumerate([*ok, False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if not found or i - start > best[1] - best[0]:
                best, found = (start, i), True
            start = None
    return best


def crop(img, box):
    """box = (x0, y0, x1, y1) as fractions of the canvas."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
