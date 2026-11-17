"""为介绍视频合成一段原创背景音乐（轻电子，120 BPM），输出 48kHz 立体声 WAV。

段落、镜头切换和点击时刻来自页面导出的时间线（render-video.mjs 会自动生成并调用本脚本）：
  intro  只有铺底和弦，慢慢升起
  light  介绍段落：铺底、琶音、轻踩镲，不加底鼓
  full   界面演示：底鼓、贝斯、琶音全开；从 lift 时刻起再叠一层高八度琶音
  outro  收束到长和弦并淡出
每拍 0.5 秒，页面上的切换点都在拍点上；镜头切换前有上扬噪声，光标点击处有轻微点击音。

    backend/.venv/bin/python demo/scripts/make-music.py --timeline demo/out/timeline.json [输出.wav]
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

args = sys.argv[1:]
timeline_path = None
if "--timeline" in args:
    i = args.index("--timeline")
    timeline_path = args[i + 1]
    del args[i : i + 2]
if timeline_path:
    TL = json.loads(Path(timeline_path).read_text())
else:  # 没有时间线时用 30 秒分析片段的旧时间线
    TL = {"duration": 30, "sections": [{"style": "intro", "t0": 0, "t1": 3}, {"style": "full", "t0": 3, "t1": 27.5},
          {"style": "outro", "t0": 27.5, "t1": 30}], "cuts": [3, 8, 13, 18, 23.5, 27.5], "clicks": [7.6, 12.6, 17.6, 23.1]}

SR = 48_000
DURATION = float(TL["duration"])
BEAT = 0.5  # 120 BPM
N = int(SR * DURATION)
rng = np.random.default_rng(7)

left = np.zeros(N)
right = np.zeros(N)


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def add(sig, start, gain=1.0, pan=0.0):
    """把单声道片段 sig 在 start 秒处叠加到左右声道，pan ∈ [-1, 1]。"""
    i = int(start * SR)
    if i < 0:  # 起点早于 0 秒时裁掉前面一段
        sig, i = sig[-i:], 0
    if i >= N:
        return
    sig = sig[: N - i] * gain
    left[i : i + len(sig)] += sig * np.sqrt((1 - pan) / 2)
    right[i : i + len(sig)] += sig * np.sqrt((1 + pan) / 2)


def env(length, attack, release, sustain_level=1.0):
    n = int(length * SR)
    e = np.full(n, sustain_level)
    a = min(int(attack * SR), n)
    r = min(int(release * SR), n - a)
    e[:a] = np.linspace(0, sustain_level, a)
    if r:
        e[n - r :] *= np.linspace(1, 0, r)
    return e


def pad(notes, length, gain=0.15):
    """柔和的合成器铺底：每个音三支略微失谐的锯齿波，经一阶低通。"""
    t = np.arange(int(length * SR)) / SR
    s = np.zeros_like(t)
    for n in notes:
        for detune in (-0.08, 0.0, 0.08):
            f = midi(n + detune)
            s += 2 * ((t * f) % 1.0) - 1
    s = lowpass(s, 900)
    return s * env(length, 0.6, 0.8) * gain / len(notes)


def pluck(note, length=0.45, gain=0.12):
    t = np.arange(int(length * SR)) / SR
    f = midi(note)
    s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    return s * np.exp(-t * 9) * env(length, 0.004, 0.05) * gain


def bass(note, length=0.9, gain=0.22):
    t = np.arange(int(length * SR)) / SR
    f = midi(note)
    s = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
    return s * env(length, 0.01, 0.25) * gain


def kick(gain=0.5):
    t = np.arange(int(0.35 * SR)) / SR
    f = 50 + 70 * np.exp(-t * 30)
    phase = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(phase) * np.exp(-t * 11) * gain


def hat(gain=0.05, decay=60):
    t = np.arange(int(0.08 * SR)) / SR
    noise = highpass(rng.standard_normal(len(t)), 7000)
    return noise * np.exp(-t * decay) * gain


def click(gain=0.18):
    t = np.arange(int(0.06 * SR)) / SR
    s = np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 90) + 0.4 * highpass(rng.standard_normal(len(t)), 4000) * np.exp(-t * 140)
    return s * gain


def swell(length=1.0, gain=0.05):
    """切换镜头前的上扬噪声。"""
    n = int(length * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    out = np.zeros(n)
    # 截止频率随时间升高的简单低通
    y = 0.0
    for k in range(0, n, 256):
        cutoff = 400 + 5000 * (k / n) ** 2
        a = np.exp(-2 * np.pi * cutoff / SR)
        seg = noise[k : k + 256]
        res = np.empty_like(seg)
        for j, x in enumerate(seg):
            y = (1 - a) * x + a * y
            res[j] = y
        out[k : k + 256] = res
    return out * (t / length) ** 2 * gain * 3


def lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def highpass(x, cutoff):
    return x - lowpass(x, cutoff)


# ---- 和声：Fmaj7 – G6 – Em7 – Am7，每和弦一小节（2 秒） ----
CHORDS = [
    (53, [65, 69, 72, 76]),  # F
    (55, [67, 71, 74, 76]),  # G
    (52, [64, 67, 71, 74]),  # Em
    (57, [64, 69, 72, 76]),  # Am
]
ARP = [0, 2, 1, 3, 2, 1, 3, 2]  # 八分音符琶音走向

def bars(t0, t1):
    """从 t0 起按小节（2 秒）迭代：(小节起点, 小节序号)；最后一个不足一小节也会给出。"""
    t, k = t0, 0
    while t < t1 - 1e-6:
        yield t, k
        t += 2.0
        k += 1


def groove(t0, t1, drums=True, lift=None):
    for t, k in bars(t0, t1):
        root, notes = CHORDS[k % 4]
        add(pad(notes, min(2.4, t1 - t + 0.4)), t)
        for b in range(4):
            bt = t + b * BEAT
            if bt >= t1:
                break
            if drums:
                add(kick(0.32 if b % 2 == 0 else 0.22), bt)
                add(hat(0.035), bt + BEAT / 2, pan=0.3)
            elif b % 2 == 1:
                add(hat(0.02), bt + BEAT / 2, pan=0.3)
            if b in (0, 2) and (drums or b == 0):
                add(bass(root - 12 + (7 if b == 2 and k % 2 else 0), 0.9 if drums else 1.8, 0.22 if drums else 0.16), bt)
        for j in range(8):
            nt = t + j * BEAT / 2
            if nt >= t1:
                break
            add(pluck(notes[ARP[j]] + 12, gain=0.12 if drums else 0.09), nt, pan=-0.35 if j % 2 else 0.35)
            if lift is not None and nt >= lift:
                add(pluck(notes[ARP[(j + 3) % 8]] + 24, 0.3, 0.045), nt + BEAT / 4, pan=0.5 if j % 2 else -0.5)


for sec in TL["sections"]:
    t0, t1, style = sec["t0"], sec["t1"], sec["style"]
    if style == "intro":
        add(pad([60, 64, 67, 71], t1 - t0 + 0.6, gain=0.24), t0)
        add(swell(t1 - t0, 0.02), t0)
        add(swell(1.0, 0.03), t1 - 1.0)
    elif style == "light":
        groove(t0, t1, drums=False)
    elif style == "full":
        add(swell(1.2, 0.035), t0 - 1.2)
        groove(t0, t1, drums=True, lift=sec.get("lift"))
    elif style == "outro":
        add(kick(0.4), t0)
        add(bass(36, t1 - t0, 0.2), t0)
        add(pad([60, 64, 67, 71, 74], t1 - t0, gain=0.22), t0)
        for k, n in enumerate([72, 76, 79, 83]):
            add(pluck(n, 1.6, 0.09), t0 + k * 0.12, pan=(k - 1.5) / 3)

# 镜头切换前的上扬、光标点击
for cut in TL["cuts"]:
    add(swell(0.9, 0.02), cut - 0.9)
for c in TL["clicks"]:
    add(click(), c)

# ---- 简易混响：几条梳状滤波器并联 ----
def reverb(x, mix=0.22):
    out = np.zeros_like(x)
    for delay_ms, fb in ((29.7, 0.72), (37.1, 0.70), (41.1, 0.68), (43.7, 0.66)):
        d = int(SR * delay_ms / 1000)
        y = np.zeros_like(x)
        for i in range(0, len(x), d):
            prev = y[i - d : i] if i >= d else np.zeros(min(d, len(x) - i))
            seg = x[i : i + d]
            y[i : i + len(seg)] = seg + fb * prev[: len(seg)]
        out += y
    return x * (1 - mix) + lowpass(out / 4, 5000) * mix


left = reverb(left)
right = reverb(right)

# 整体淡入淡出、归一化到 -3 dBFS
fade = np.ones(N)
fade[: int(0.3 * SR)] = np.linspace(0, 1, int(0.3 * SR))
fade[-int(1.5 * SR) :] = np.linspace(1, 0, int(1.5 * SR)) ** 1.5
stereo = np.stack([left, right], axis=1) * fade[:, None]
stereo *= 0.707 / np.max(np.abs(stereo))

out = Path(args[0] if args else Path(__file__).parent.parent / "audio" / "bgm.wav")
out.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(out), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((stereo * 32767).astype("<i2").tobytes())
print(out)
