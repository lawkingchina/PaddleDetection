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
from __future__ import print_function

import math
try:
    from collections.abc import Sequence
except Exception:
    from collections import Sequence

import numpy as np

__all__ = ['Sampler']


class Sampler(object):
    def __init__(self,
                 dataset,
                 batch_size,
                 shuffle=True,
                 aspect_ratio_thresholds=None,
                 pad_batch=True,
                 sync_seed_schedule=True,
                 rank=0,
                 world_size=1,
                 init_seed=1):
        super(Sampler, self).__init__()
        assert not aspect_ratio_thresholds or \
            isinstance(aspect_ratio_thresholds, Sequence), \
            "if given, aspect_ratio_thresholds must be a sequence"
        assert pad_batch or not aspect_ratio_thresholds, \
            "`pad_batch` must be enabled when grouping by aspect ratio"
        self.dataset = dataset
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.aspect_ratio_thresholds = aspect_ratio_thresholds
        self.pad_batch = pad_batch
        self.sync_seed_schedule = sync_seed_schedule
        self.rank = rank
        self.world_size = world_size
        self.init_seed = init_seed
        self.epoch = 0
        self._step = 0

    def setup(self):
        whole_batch_size = self.world_size * self.batch_size
        if self.world_size > 1 and not self.sync_seed_schedule:
            print("Disabling `sync_seed_schedule` is not recommended for"
                  + "distributed training, you may want to reconsider")
        if self.aspect_ratio_thresholds is None:
            self.num_batches = math.ceil(len(self.dataset) / whole_batch_size)
            return self

        assert hasattr(self.dataset, 'aspect_ratios'), \
            "aspect_ratio_thresholds is set, " + \
            "but dataset does not provide aspect ratio info"

        self.bucket_flags = np.digitize(self.dataset.aspect_ratios,
                                        self.aspect_ratio_thresholds)
        self.bucket_sizes = np.bincount(self.bucket_flags)
        self.pad_lengths = [
            int(math.ceil(s / whole_batch_size) * whole_batch_size) - s
            for s in self.bucket_sizes]
        self.num_batches = sum([int(math.ceil(s / whole_batch_size))
                                for s in self.bucket_sizes])

    def reset(self):
        if not hasattr(self, 'num_batches'):
            self.setup()
        if not self.shuffle:
            def rand_perm(x):
                return x
        elif self.sync_seed_schedule:
            seed = self.epoch + self.init_seed
            rand_perm = np.random.RandomState(seed).permutation
            # XXX do not use with `itertools.cycle`,
            # should work fine for regular for loops or enumerate()
            self.epoch += 1
        else:
            rand_perm = np.random.permutation

        whole_batch_size = self.world_size * self.batch_size
        if self.aspect_ratio_thresholds:
            shuffled_indices = []
            for idx, (size, pad) in enumerate(
                    zip(self.bucket_sizes, self.pad_lengths)):
                if size == 0:
                    continue
                bucket = np.where(self.bucket_flags == idx)[0]
                shuffled = list(rand_perm(bucket))
                shuffled += shuffled[:pad]
                shuffled_indices += shuffled
        else:
            shuffled_indices = list(rand_perm(np.arange(len(self.dataset))))
            pad = len(self) * whole_batch_size - len(self.dataset)
            if self.pad_batch:
                shuffled_indices += shuffled_indices[:pad]
            else:
                shuffled_indices += [-1] * pad

        # shuffle by small batch, i.e., draw each small batch from same bucket
        shape = [-1, self.batch_size]

        # shuffle along batch index then split by number of shards
        batches = np.array(shuffled_indices).reshape(*shape)
        shuffled_shards = rand_perm(batches).reshape(
            self.world_size, -1, self.batch_size)
        self._shard = shuffled_shards[self.rank].tolist()
        self._step = 0

    def __iter__(self):
        if not hasattr(self, 'num_batches'):
            self.setup()
        if not hasattr(self, '_shard'):
            self.reset()
        return self

    def __next__(self):
        if self._step >= self.num_batches:
            raise StopIteration
        if self.pad_batch:
            ids = self._shard[self._step]
        else:
            ids = [idx for idx in self._shard[self._step] if idx != -1]
        self._step += 1
        return ids

    # python 2 compatibility
    next = __next__

    def __len__(self):
        if not hasattr(self, 'num_batches'):
            self.setup()
        return self.num_batches
