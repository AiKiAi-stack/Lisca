import av
import sys

def probe(path):
    c = av.open(path)
    dur = float(c.duration) / 1e6 if c.duration else None
    print(f"\n=== {path} 时长: {dur:.1f}s 大小: {c.size/1e6:.0f}MB ===")
    for i, s in enumerate(c.streams):
        if s.type == 'audio':
            lang = s.language or (s.metadata.get('language') if s.metadata else None)
            print(f"  audio[{i}]: {s.codec_context.name} {s.rate}Hz ch={s.channels} lang={lang}")

for p in sys.argv[1:]:
    probe(p)
