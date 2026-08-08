import av
import sys

def extract(src, audio_index, t_start, t_dur, dst):
    c = av.open(src)
    stream = c.streams.audio[audio_index]
    if t_start > 0:
        c.seek(int(t_start * 1e6))
    resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    out = av.open(dst, 'w')
    out_stream = out.add_stream('pcm_s16le', rate=16000)
    out_stream.layout = 'mono'
    samples_written = 0
    for frame in c.decode(stream):
        for rf in resampler.resample(frame):
            for pkt in out_stream.encode(rf):
                out.mux(pkt)
            samples_written += rf.samples
        if samples_written >= t_dur * 16000:
            break
    for pkt in out_stream.encode(None):
        out.mux(pkt)
    out.close()
    c.close()
    print(f"done: {dst} ({t_dur}s @16k mono)")

if __name__ == '__main__':
    extract(sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5])
