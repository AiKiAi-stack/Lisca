from dataclasses import dataclass

import av
import numpy as np


@dataclass
class AudioProcessor:
    target_sample_rate: int = 16000
    target_format: str = "s16"
    target_layout: str = "mono"

    def __post_init__(self) -> None:
        self._resampler = av.AudioResampler(
            format=self.target_format,
            layout=self.target_layout,
            rate=self.target_sample_rate,
        )

    def process(self, frame: av.AudioFrame) -> np.ndarray:
        out_frames = self._resampler.resample(frame)
        arrays = []
        for f in out_frames:
            arr = f.to_ndarray()
            arr = arr.squeeze()
            arr = arr.astype(np.int16)
            arrays.append(arr)
        if len(arrays) == 1:
            return arrays[0]
        return np.concatenate(arrays)
