# Copyright (c) 2019 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import division

try:
    from collections.abc import Mapping, Sequence
except Exception:
    from collections import Mapping, Sequence

import numpy as np
from PIL import Image, ImageEnhance


__all__ = ['RandomFlip', 'RandomExpand', 'RandomCrop', 'ColorDistort',
           'MixUp', 'Resize', 'NormalizePermute', 'NormalizeLabels',
           'PadToStride', 'ToFeedDict']


class Resize(object):
    def __init__(self,
                 target_dim=None,
                 max_dim=None,
                 random_shape=[],
                 interp=Image.BILINEAR):
        super(Resize, self).__init__()
        self.target_dim = target_dim
        self.max_dim = max_dim
        self.random_shape = random_shape
        self.interp = interp  # 'random' for yolov3

    @property
    def batch_seed(self):
        return bool(self.random_shape)

    def __call__(self, sample):
        img = sample['image']
        w = sample['width']
        h = sample['height']
        if isinstance(img, np.ndarray):
            img = Image.fromarray(img.astype(np.uint8))

        interp = self.interp
        if interp == 'random':
            # `BOX` and `HAMMING` was added in pillow 3.4
            avail_interp = 'HAMMING' in Image.__dict__ and 4 or 6
            interp = np.random.choice(range(avail_interp - 1))

        if self.random_shape:
            assert 'batch_seed' in sample, "random_shape requires batch_seed"
            seed = sample['batch_seed']
            dim = np.random.RandomState(seed).choice(self.random_shape)
            resize_w = resize_h = dim
            scale_x = dim / w
            scale_y = dim / h
            # XXX this is for YOLOv3 and SSD, bboxes are scaled
            scale_array = np.array([scale_x, scale_y, scale_x, scale_y],
                                   dtype=np.float32)
            sample['gt_box'] *= scale_array
        else:
            target_dim = self.target_dim
            if isinstance(self.target_dim, Sequence):
                target_dim = np.random.choice(target_dim)

            dim_min, dim_max = w > h and (w, h) or (h, w)
            scale = min(dim_max / self.max_dim, dim_min / target_dim)
            resize_w = round(w * scale)
            resize_h = round(h * scale)
            sample['scale'] = scale
            # XXX this is for RCNN
            # commonly the labels (bboxes and masks) are scaled by the
            # dataloader, but somehow Paddle choose to do it later.
            # That is why we need to pass "scale" around, and this also results
            # in some other caveats, e.g., all transformations that modifies
            # bboxes (currently `RandomFlip`) must be applied BEFORE `Resize`.

        sample['image'] = img.resize((resize_w, resize_h), interp)
        sample['width'] = resize_w
        sample['height'] = resize_h
        return sample


class RandomFlip(object):
    def __init__(self, prob=.5):
        super(RandomFlip, self).__init__()
        self.prob = prob

    def __call__(self, sample):
        if np.random.uniform(0., 1.) < self.prob:
            return sample

        img = sample['image']
        gt_box = sample['gt_box']

        if isinstance(img, Image.Image):
            sample['image'] = img.transpose(Image.FLIP_LEFT_RIGHT)
            w = img.size[0]
        else:
            sample['image'] = img[:, ::-1, :]
            w = img.shape[1]

        gt_box[:, 0] = w - gt_box[:, 0]
        gt_box[:, 2] = w - gt_box[:, 2]
        sample['gt_box'] = gt_box

        if 'gt_poly' in sample:
            poly = np.array(sample['gt_poly'])
            poly[0::2] = w - np.array(poly[0::2]) - 1
            sample['gt_poly'] = poly
        return sample


class ColorDistort(object):
    def __init__(self,
                 hue=[-18, 18, 0.5],
                 saturation=[0.5, 1.5, 0.5],
                 contrast=[0.5, 1.5, 0.5],
                 brightness=[0.5, 1.5, 0.5]):
        super(ColorDistort, self).__init__()
        self.hue = hue
        self.saturation = saturation
        self.contrast = contrast
        self.brightness = brightness

    def apply_hue(self, img):
        low, high, prob = self.hue
        if np.random.uniform(0., 1.) < prob:
            return img

        if isinstance(img, Image.Image):
            img = np.asarray(img)
        img = img.astype(np.float32)

        # XXX works, but result differ from HSV version
        delta = np.random.uniform(low, high)
        u = np.cos(delta * np.pi)
        w = np.sin(delta * np.pi)
        bt = np.array([[1.0, 0.0, 0.0],
                       [0.0, u, -w],
                       [0.0, w, u]])
        tyiq = np.array([[0.299, 0.587, 0.114],
                         [0.596, -0.274, -0.321],
                         [0.211, -0.523, 0.311]])
        ityiq = np.array([[1.0, 0.956, 0.621],
                          [1.0, -0.272, -0.647],
                          [1.0, -1.107, 1.705]])
        t = np.dot(np.dot(ityiq, bt), tyiq).T
        img = np.dot(img, t)
        return img

    def apply_saturation(self, img):
        low, high, prob = self.saturation
        if np.random.uniform(0., 1.) < prob:
            return img
        delta = np.random.uniform(low, high)

        if isinstance(img, Image.Image):
            return ImageEnhance.Contrast(img).enhance(delta)
        img = img.astype(np.float32)
        gray = img * np.array([[[0.299, 0.587, 0.114]]], dtype=np.float32)
        gray = gray.sum(axis=2, keepdims=True)
        gray *= (1.0 - delta)
        img *= delta
        img += gray
        return img

    def apply_contrast(self, img):
        low, high, prob = self.contrast
        if np.random.uniform(0., 1.) < prob:
            return img
        delta = np.random.uniform(low, high)

        if isinstance(img, Image.Image):
            return ImageEnhance.Color(img).enhance(delta)
        img = img.astype(np.float32)
        img *= delta
        return img

    def apply_brightness(self, img):
        low, high, prob = self.brightness
        if np.random.uniform(0., 1.) < prob:
            return img
        delta = np.random.uniform(low, high)

        if isinstance(img, Image.Image):
            return ImageEnhance.Brightness(img).enhance(delta)
        img = img.astype(np.float32)
        img += delta
        return img

    def __call__(self, sample):
        img = sample['image']
        img = self.apply_brightness(img)

        if np.random.randint(0, 2):
            img = self.apply_contrast(img)
            img = self.apply_saturation(img)
            img = self.apply_hue(img)
        else:
            img = self.apply_saturation(img)
            img = self.apply_hue(img)
            img = self.apply_contrast(img)
        sample['image'] = img
        return sample


class NormalizePermute(object):
    def __init__(self, mean=[0.485, 0.456, 0.406], std=[1, 1, 1]):
        super(NormalizePermute, self).__init__()
        self.mean = mean
        self.std = std

    def __call__(self, sample):
        img = sample['image']
        if isinstance(img, Image.Image):
            img = np.asarray(img)
        img = img.astype(np.float32)

        img = img.transpose((2, 0, 1))
        img.__imul__(1. / 255)
        mean = np.array(self.mean, dtype=np.float32)
        std = np.array(self.std, dtype=np.float32)
        invstd = 1. / std
        for v, m, s in zip(img, mean, invstd):
            v.__isub__(m).__imul__(s)
        sample['image'] = img
        return sample


class RandomExpand(object):
    def __init__(self, ratio=4., prob=0.5, fill_value=(127.5,) * 3):
        super(RandomExpand, self).__init__()
        assert ratio > 1.01, "expand ratio must be larger than 1.01"
        self.ratio = ratio
        self.prob = prob
        assert isinstance(fill_value, (float, Sequence)), \
            "fill value must be either float or sequence"
        if isinstance(fill_value, float):
            fill_value = (fill_value,) * 3
        if not isinstance(fill_value, tuple):
            fill_value = tuple(fill_value)
        self.fill_value = fill_value

    def __call__(self, sample):
        if np.random.uniform(0., 1.) < self.prob:
            return sample

        img = sample['image']
        height = sample['height']
        width = sample['width']

        expand_ratio = np.random.uniform(1.01, self.ratio)
        h = int(height * expand_ratio)
        w = int(width * expand_ratio)
        y = np.random.randint(0, h - height)
        x = np.random.randint(0, w - width)
        if isinstance(img, Image.Image):
            fill_value = (int(f) for f in self.fill_value)
            canvas = Image.new('RGB', (w, h), fill_value)
            canvas.paste(img, (x, y))
        else:
            canvas = np.ones((h, w, 3), dtype=np.float32)
            canvas *= np.array(self.fill_value, dtype=np.float32)
            canvas[y:y + height, x:x + width, :] = img.astype(np.float32)

        sample['height'] = h
        sample['width'] = w
        sample['image'] = canvas
        sample['gt_box'] += np.array([x, y, x, y], dtype=np.float32)
        return sample


class RandomCrop(object):
    def __init__(self,
                 aspect_ratio=[.5, 2.],
                 thresholds=[.0, .1, .3, .5, .7, .9],
                 scaling=[.3, 1.],
                 num_attempts=50,
                 allow_no_crop=True):
        super(RandomCrop, self).__init__()
        self.aspect_ratio = aspect_ratio
        self.thresholds = thresholds
        self.scaling = scaling
        self.num_attempts = num_attempts
        self.allow_no_crop = allow_no_crop

    def __call__(self, sample):
        h = sample['height']
        w = sample['width']
        gt_box = sample['gt_box']

        # NOTE Original method attempts to generate one candidate for each
        # threshold then randomly sample one from the resulting list.
        # Here a short circuit approach is taken, i.e., randomly choose a
        # threshold and attempt to find a valid crop, and simply return the
        # first one found.
        # The probability is not exactly the same, kinda resembling the
        # "Monty Hall" problem. Actually carrying out the attempts will affect
        # observability (just like opening doors in the "Monty Hall" game).
        thresholds = self.thresholds.copy()
        if self.allow_no_crop:
            thresholds.append('no_crop')
        np.random.shuffle(thresholds)

        for thresh in thresholds:
            if thresh == 'no_crop':
                return sample

            found = False
            for i in range(self.num_attempts):
                scale = np.random.uniform(*self.scaling)
                min_ar, max_ar = self.aspect_ratio
                aspect_ratio = np.random.uniform(max(min_ar, scale**2),
                                                 min(max_ar, scale**-2))
                crop_h = int(h * scale / np.sqrt(aspect_ratio))
                crop_w = int(w * scale * np.sqrt(aspect_ratio))
                crop_y = np.random.randint(0, h - crop_h)
                crop_x = np.random.randint(0, w - crop_w)
                crop_box = [crop_x, crop_y, crop_x + crop_w, crop_y + crop_h]
                iou = self._iou_matrix(gt_box,
                                       np.array([crop_box], dtype=np.float32))
                if iou.min() < thresh:
                    continue

                cropped_box, valid_ids = self._crop_box_with_center_constraint(
                    gt_box, np.array(crop_box, dtype=np.float32))
                if valid_ids.size > 0:
                    found = True
                    break

            if found:
                sample['image'] = self._crop_image(sample['image'], crop_box)
                sample['gt_box'] = np.take(cropped_box, valid_ids, axis=0)
                sample['gt_label'] = np.take(sample['gt_label'], valid_ids)
                sample['width'] = crop_box[2] - crop_box[0]
                sample['height'] = crop_box[3] - crop_box[1]
                if 'gt_score' in sample:
                    sample['gt_score'] = np.take(
                        sample['gt_score'], valid_ids)
                return sample

        return sample

    def _iou_matrix(self, a, b):
        tl_i = np.maximum(a[:, np.newaxis, :2], b[:, :2])
        br_i = np.maximum(a[:, np.newaxis, 2:], b[:, 2:])

        area_i = np.prod(br_i - tl_i, axis=2) * (tl_i < br_i).all(axis=2)
        area_a = np.prod(a[:, 2:] - a[:, :2], axis=1)
        area_b = np.prod(b[:, 2:] - b[:, :2], axis=1)
        area_o = (area_a[:, np.newaxis] + area_b - area_i)
        return area_i / area_o

    def _crop_box_with_center_constraint(self, box, crop):
        cropped_box = box.copy()

        cropped_box[:, :2] = np.maximum(box[:, :2], crop[:2])
        cropped_box[:, 2:] = np.minimum(box[:, 2:], crop[2:])
        cropped_box[:, :2] -= crop[:2]
        cropped_box[:, 2:] -= crop[:2]

        centers = (box[:, :2] + box[:, 2:]) / 2
        valid = np.logical_and(
            crop[:2] <= centers, centers < crop[2:]).all(axis=1)
        valid = np.logical_and(
            valid, (cropped_box[:, :2] < cropped_box[:, 2:]).all(axis=1))

        return cropped_box, np.where(valid)[0]

    def _crop_image(self, img, crop):
        if isinstance(img, Image.Image):
            return img.crop(crop)
        else:
            x1, y1, x2, y2 = crop
            return img[y1:y2, x1:x2, :]


class MixUp(object):
    def __init__(self, alpha=1.5, beta=1.5):
        super(MixUp, self).__init__()
        assert alpha > 0., "alpha should be positive"
        assert beta > 0., "beta should be positive"
        self.alpha = alpha
        self.beta = beta
        self.is_mixup = True

    def __call__(self, sample1, sample2):
        factor = np.clip(np.random.beta(self.alpha, self.beta), 0., 1.)
        if factor == 1.:
            return sample1
        if factor == 0.:
            return sample2

        gt_box1, gt_box2 = sample1['gt_box'], sample2['gt_box']
        gt_label1, gt_label2 = sample1['gt_label'], sample2['gt_label']
        gt_score1, gt_score2 = sample1['gt_score'], sample2['gt_score']
        gt_box = np.concatenate((gt_box1, gt_box2), axis=0)
        gt_label = np.concatenate((gt_label1, gt_label2), axis=0)
        gt_score = np.concatenate((gt_score1, gt_score2), axis=0)

        img1, img2 = sample1['image'], sample2['image']
        w1, h1 = img1.size
        w2, h2 = img2.size
        w = max(w1, w2)
        h = max(h1, h2)

        if isinstance(img1, Image.Image):
            img1 = np.asarray(img1, dtype=np.float32)
        if isinstance(img2, Image.Image):
            img2 = np.asarray(img2, dtype=np.float32)
        img1 = img1.astype(np.float32)
        img2 = img2.astype(np.float32)

        canvas = np.zeros((h, w, 3), dtype=np.float32)
        canvas[:h1, :w1, :] = img1 * factor
        canvas[:h2, :w2, :] += img2 * (1. - factor)

        sample1['image'] = canvas
        sample1['gt_box'] = gt_box
        sample1['gt_label'] = gt_label
        sample1['gt_score'] = gt_score
        sample1['width'] = w
        sample1['height'] = h

        return sample1


class NormalizeLabels(object):
    def __init__(self, num_instances=50, normalize_box=True):
        super(NormalizeLabels, self).__init__()
        self.num_instances = num_instances
        self.normalize_box = normalize_box

    def __call__(self, sample):
        if self.normalize_box:
            w = sample['width']
            h = sample['height']
            sample['gt_box'] /= np.array([w, h] * 2, dtype=np.float32)
        if self.num_instances is None:
            return sample

        # cap then pad labels
        gt_box = sample['gt_box'][:self.num_instances, :]
        gt_label = sample['gt_label'][:self.num_instances]
        pad = self.num_instances - gt_label.size
        gt_box_padded = np.pad(gt_box, ((0, pad), (0, 0)))
        gt_label_padded = np.pad(gt_label, [(0, pad)])
        sample['gt_box'] = gt_box_padded
        sample['gt_label'] = gt_label_padded

        if 'gt_score' in sample:
            gt_score = sample['gt_score'][:self.num_instances]
            gt_score_padded = np.pad(gt_score, [(0, pad)])
            sample['gt_score'] = gt_score_padded

        return sample


class PadToStride(object):
    def __init__(self, stride=1):
        super(PadToStride, self).__init__()
        assert stride > 0, "stride must be greater than zero"
        self.stride = stride

    def __call__(self, batch):
        images = batch['image']
        assert isinstance(images[0], np.ndarray), "images must be ndarrays"

        batch_size = len(images)
        dims = [i.shape for i in images]
        hs = [dim[1] for dim in dims]
        ws = [dim[2] for dim in dims]
        pad_h = max(hs)
        pad_w = max(ws)
        pad_h = ((pad_h + self.stride - 1) // self.stride) * self.stride
        pad_w = ((pad_w + self.stride - 1) // self.stride) * self.stride
        chan = dims[0][0]

        padded = np.zeros((batch_size, chan, pad_h, pad_w), dtype=np.float32)
        for idx, img in enumerate(images):
            padded[idx, :, :hs[idx], :ws[idx]] = img

        batch['image'] = padded
        batch['padded_height'] = np.array([pad_h] * batch_size)
        batch['padded_width'] = np.array([pad_w] * batch_size)
        return batch


class ToFeedDict(object):
    def __init__(self,
                 feed_vars=[],
                 extra_vars=[],
                 pin_memory=False):

        super(ToFeedDict, self).__init__()
        self.feed_vars = feed_vars
        self.extra_vars = extra_vars
        self.pin_memory = pin_memory

        self._normalized_vars = []
        for var in self.feed_vars:
            if isinstance(var, str):
                name = var
                fields = [var]
                lod_level = 0
            else:
                assert isinstance(var, Mapping), \
                    "feed_var should be either string or dict like object"
                name = var['name']
                if 'fields' in var:
                    fields = var['fields']
                else:
                    fields = [name]
                lod_level = 'lod_level' in var and var['lod_level'] or 0
            self._normalized_vars.append({
                'name': name,
                'fields': fields,
                'lod_level': lod_level})

    def __call__(self, sample):
        extra_dict = {key: sample[key] for key in self.extra_vars}
        feed_dict = {}

        for var in self._normalized_vars:
            name = var['name']
            lod_level = var['lod_level']
            fields = var['fields']

            array_list = []
            seq_length = []

            for idx, f in enumerate(fields):
                arr = sample[f]
                # assume all fields have the same LoD and seq_length
                if lod_level != 0 and idx == 0:
                    seq_length = self._recursive_length(arr, lod_level + 1)
                if isinstance(arr, np.ndarray):
                    # 'image' may already be stacked by `PadToStride`
                    array_list.append(arr)
                else:
                    if all([isinstance(a, np.ndarray) for a in arr]):
                        if lod_level == 0:
                            array_list.append(np.stack(arr))
                        else:
                            array_list.append(np.concatenate(arr))
                    else:
                        array_list.append(np.asarray(arr))

            if len(fields) == 1:
                ndarray = array_list[0]
            else:
                # combine fields
                ndarray = np.stack(array_list).T
            feed_dict[name] = self._to_tensor(ndarray, seq_length)

        return feed_dict, extra_dict

    def _to_tensor(self, ndarray, seq_length):
        from paddle import fluid
        place = self.pin_memory and fluid.CUDAPinnedPlace() or fluid.CPUPlace()
        t = fluid.core.LoDTensor()
        t.set_recursive_sequence_lengths(seq_length)
        t.set(ndarray, place)
        return t

    def _recursive_length(self, ndarray, lod_level):
        if isinstance(ndarray, np.ndarray) and ndarray.ndim >= lod_level:
            # handle dense numpy array
            shape = ndarray.shape
            last = 1
            seq_length = []
            for i in range(lod_level):
                cur = shape[i]
                seq_length.append([cur] * last)
                last *= cur
        else:
            seq_length = [[] for _ in range(lod_level)]

            def _recurse(data, result, level):
                if level > 0:
                    result[0].append(len(data))
                    for item in data:
                        _recurse(item, result[1:], level - 1)
            _recurse(ndarray, seq_length, lod_level)

        return seq_length