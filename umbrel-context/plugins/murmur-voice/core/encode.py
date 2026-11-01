"""Speech as AAC in fragmented MP4, each piece sent as soon as it's made. On an iPhone, any Web Audio
beside the mic's echo cancelling makes speech crackle, so Murmur plays calls there through an audio
element, fed through Managed Media Source, which takes this and not raw PCM.
"""

from __future__ import annotations

import io
from typing import Iterable, Iterator

from . import engines


def fragments(pcm: Iterable[bytes]) -> Iterator[bytes]:
    """16-bit mono PCM at the stream rate, as MP4 fragments."""
    import av
    import numpy as np

    out = io.BytesIO()
    container = av.open(out, "w", format="mp4", options={"movflags": "empty_moov+default_base_moof+frag_every_frame"})
    stream = container.add_stream("aac", rate=engines.STREAM_RATE, layout="mono")
    stream.bit_rate = 64_000
    written = 0

    def ready() -> Iterator[bytes]:
        if out.tell():
            yield out.getvalue()
            out.seek(0)
            out.truncate()

    for chunk in pcm:
        samples = np.frombuffer(chunk, "<i2").reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate, frame.pts = engines.STREAM_RATE, written
        written += samples.shape[1]
        for packet in stream.encode(frame):
            container.mux(packet)
        yield from ready()
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    yield from ready()
