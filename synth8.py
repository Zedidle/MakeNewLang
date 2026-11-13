# -*- coding: utf-8 -*-
"""Thelshaat 线语 8-Bit 合成器 v1 —— 以芬兰语元音为蓝本的"游戏机声线"。

与 tts.py 六套神经声线同款（同 14 句例句 + 音素示范），但完全不走 edge-tts，
在本地用 8-bit 游戏机音频的工艺渲染：

  声源      NES APU 式脉冲波（25% 占空比；鼻音/边音 50%、闪音 12.5%）+ LFSR 噪声声道
  元音蓝本  芬兰语八元音 a ä o ö u y e i 的共振峰（本语言只用 a e i o u，
            ä ö y 在 00_demo_vowels 里作为色板展示）
  量化      音高锁 12 平均律半音格、共振峰走 40/80/160Hz 台阶、
            幅度 16 级（NES 音量包络）、控制参数 60Hz 帧更新
  输出      11025 Hz / 8-bit PCM WAV，audio/8bit/，内嵌 player8.html

用法:
    python synth8.py

Token 结构与 synth.py 完全一致，直接复用其分词/语调机器。
"""
import base64
import math
import os
import random
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import synth as base          # 复用 token / build_events / Inton / _path_at / DUR / STOPS / NAS / LIQ
from tts import PHRASES as TTS_PHRASES, META   # 同款 14 句语料

SR = 11025
DT = 1.0 / SR
FRAME = SR // 60              # 60Hz 控制帧（NES 帧率）
random.seed(8)

# ------------------------------------------------- 芬兰语元音共振峰（蓝本）
# a /ɑ/  e /e/  i /i/  o /o/  u /u/ 为本语言所用；ä /æ/ ö /ø/ y /y/ 为色板展示。
# 扁平 6 元组: f1,b1,f2,b2,f3,b3
VOW8 = {
    'a': (700, 100, 1150, 110, 2500, 160),
    'e': (430, 90, 1900, 120, 2500, 160),
    'i': (280, 70, 2200, 130, 2900, 180),
    'o': (440, 90, 800, 100, 2500, 160),
    'u': (320, 80, 700, 100, 2400, 160),
    'ä': (700, 100, 1700, 120, 2500, 160),
    'ö': (430, 90, 1500, 120, 2500, 160),
    'y': (280, 70, 1900, 120, 2900, 180),
}
DIP8 = {'ai': ('a', 'i'), 'ei': ('e', 'i'), 'au': ('a', 'u'),
        'ou': ('o', 'u'), 'ia': ('i', 'a'), 'ua': ('u', 'a')}

DUTY = {'v': 0.25, 'dip': 0.25, 'nas': 0.50, 'l': 0.50, 'r': 0.125, 'glide': 0.25}
AMPK = {'v': 1.00, 'dip': 1.00, 'nas': 0.70, 'l': 0.90, 'r': 0.55, 'glide': 0.90}

VOICED = base.VOICED
TR = base.TR


# ------------------------------------------------- 基础件
class Res(object):
    """二阶谐振器（本文件自己的 DT，11025Hz）。"""
    __slots__ = ('a', 'b', 'c', 'y1', 'y2')

    def __init__(self):
        self.a = self.b = self.c = 0.0
        self.y1 = self.y2 = 0.0

    def set(self, f, bw):
        f = min(f, 0.42 * SR)
        r = math.exp(-math.pi * bw * DT)
        th = 2 * math.pi * f * DT
        self.c = -r * r
        self.b = 2 * r * math.cos(th)
        self.a = 1 - self.b - self.c

    def proc(self, x):
        y = self.a * x + self.b * self.y1 + self.c * self.y2
        self.y2 = self.y1
        self.y1 = y
        return y


class LFSR(object):
    """15 位线性反馈移位寄存器噪声（NES 噪声声道同款工艺）。"""
    __slots__ = ('r',)

    def __init__(self, seed=0x7FFF):
        self.r = seed

    def next(self):
        b = (self.r ^ (self.r >> 1)) & 1
        self.r = (self.r >> 1) | (b << 14)
        return 1.0 if self.r & 1 else -1.0


def snap_sem(f):
    """音高锁到 A3=220Hz 的 12 平均律半音格。"""
    if f <= 20:
        return f
    n = round(12 * math.log2(f / 220.0))
    return 220.0 * 2 ** (n / 12.0)


def qform(t):
    """共振峰量化成台阶：F1 步进 40Hz、F2 步进 80Hz、F3 步进 160Hz。"""
    return (round(t[0] / 40) * 40, t[1],
            round(t[2] / 80) * 80, t[3],
            round(t[4] / 160) * 160, t[5])


def tgt(kind, params, which):
    if kind == 'v':
        return VOW8[params[0]]   # 长元音 'aa' 与短元音同目标，长度由时长承担
    if kind == 'dip':
        va, vb = DIP8[params]
        return VOW8[va] if which == 'start' else VOW8[vb]
    if kind == 'nas':
        return base.NAS[params]
    return base.LIQ[params]   # l / r / glide


# ------------------------------------------------- 渲染
def render(events, inton, whisper=False):
    total = events[-1][0] + events[-1][1]
    n_total = int(total * SR) + SR // 50
    buf = [0.0] * n_total

    ne = len(events)
    prev_adj = [None] * ne
    next_any = [None] * ne
    scan = None
    for i in range(ne - 1, -1, -1):
        k, p = events[i][2], events[i][3]
        if k in VOICED:
            scan = tgt(k, p, 'start')
        next_any[i] = scan
    for i in range(ne):
        if i > 0 and events[i - 1][2] in VOICED:
            pk, pp = events[i - 1][2], events[i - 1][3]
            prev_adj[i] = tgt(pk, pp, 'end')

    for ei in range(ne):
        start, dur, kind, params, flags = events[ei]
        base_i = int(start * SR)
        n = max(1, int(dur * SR))
        stress = 'stress' in flags
        final = 'final' in flags

        # ---- 浊音段：脉冲波（或耳语时噪声）过三共振峰，60Hz 帧更新
        if kind in VOICED:
            va = tgt(kind, params, 'start')
            vb = tgt(kind, params, 'end')
            path = []
            if prev_adj[ei] is not None and dur > 2 * TR:
                path.append((0.0, prev_adj[ei]))
                path.append((TR, va))
            elif prev_adj[ei] is not None:
                path.append((0.0, prev_adj[ei]))
                path.append((min(TR, dur * 0.5), va))
            else:
                path.append((0.0, va))
            if dur > 2 * TR:
                path.append((dur - TR, vb))
            else:
                path.append((dur * 0.9, vb))
            path.append((dur, vb))

            amp = AMPK[kind]
            if stress and kind in ('v', 'dip'):
                amp *= 1.2
            if whisper:
                amp *= 1.9
            amp_q = round(amp * 15) / 15.0          # 16 级音量（NES 包络）

            f0a = inton.f0(start)
            fdelta = inton.final_delta() if final else 0.0
            duty = DUTY[kind]
            rs = [Res() for _ in range(3)]
            phase = random.random()
            lfsr = LFSR()

            for i0 in range(0, n, FRAME):
                i1 = min(i0 + FRAME, n)
                tt = (i0 + i1) / 2.0 / n * dur
                cur = qform(base._path_at(path, tt))
                for k in range(3):
                    rs[k].set(cur[2 * k], cur[2 * k + 1])
                f = f0a
                x = tt / dur if dur > 0 else 0.0
                if stress and kind in ('v', 'dip'):
                    f += 16 * math.sin(math.pi * x)
                if final:
                    sm = x * x * (3 - 2 * x)
                    f += fdelta * sm
                f = snap_sem(f)                      # 音高锁半音格
                for i in range(i0, i1):
                    x = i / n
                    env = 1.0
                    if x < 0.012:
                        env = x / 0.012
                    elif x > 0.96:
                        env = (1 - x) / 0.04
                    if whisper:
                        s = lfsr.next() * 0.5
                    else:
                        phase += f * DT
                        if phase >= 1:
                            phase -= 1
                        s = 1.0 if phase < duty else -1.0
                    y = rs[0].proc(s) + rs[1].proc(s) + rs[2].proc(s)
                    buf[base_i + i] += y * amp_q * env

        # ---- 清擦音：LFSR 噪声过双谐振器（s 超出奈奎斯特则降频）
        elif kind == 'fric':
            f, bw, a = params
            if f >= 0.45 * SR:
                f, bw = 4600, 1500
            r1 = Res(); r1.set(f, bw)
            r2 = Res(); r2.set(f * 0.9, bw * 1.3)
            lfsr = LFSR()
            for i in range(n):
                x = lfsr.next()
                v = (r1.proc(x) + r2.proc(x)) * 0.5 * a
                if i < 0.005 * SR:
                    v *= i / (0.005 * SR)
                buf[base_i + i] += v

        # ---- h / 送气：噪声染上下一个元音的共振峰（协同发音）
        elif kind in ('h', 'asp'):
            t = next_any[ei] or base.NEUTRAL
            r1 = Res(); r1.set(t[0], t[1] * 2.2)
            r2 = Res(); r2.set(t[2], t[3] * 2.2)
            amp = 0.25 if kind == 'asp' else 0.28
            lfsr = LFSR()
            for i in range(n):
                x = lfsr.next()
                v = (r1.proc(x) + r2.proc(x)) * 0.5 * amp
                if i < 0.006 * SR:
                    v *= i / (0.006 * SR)
                buf[base_i + i] += v

        # ---- 爆破：极短噪声 ping（8-bit 爆点）
        elif kind == 'burst':
            f, bw, a = params
            r1 = Res(); r1.set(min(f, 0.42 * SR), bw)
            lfsr = LFSR()
            for i in range(n):
                v = r1.proc(lfsr.next()) * a
                buf[base_i + i] += v
        # sil / pause：缓冲区默认为零

    # 软限幅 + 峰值归一化
    for i in range(n_total):
        x = buf[i]
        buf[i] = math.tanh(1.3 * x) / math.tanh(1.3)
    peak = max(abs(x) for x in buf) or 1.0
    g = 0.85 / peak
    return [x * g for x in buf]


def write_wav8(path, data):
    """8-bit 无符号 PCM WAV（11025Hz 单声道）。"""
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(SR)
        w.writeframes(bytes(min(255, max(0, 128 + int(x * 127))) for x in data))


# ------------------------------------------------- 语料
MODES = {4: 'emph', 9: 'whisper', 11: 'slow', 13: 'chant'}
FIN_SHOWCASE = ['a', 'ä', 'e', 'i', 'o', 'ö', 'u', 'y',
                'aa', 'ää', 'ee', 'ii', 'oo', 'öö', 'uu', 'yy']
PHONEME_DEMO = ('aa ee ii oo uu, pa ta tha ka kha tsa sa sha ha '
                'ma na la ra ja wa, ai au ei ou, tel haal paat')


def vowel_events(seq):
    ev, t = [], 0.0
    for v in seq:
        d = 0.22 if len(v) == 2 else 0.14
        ev.append((t, d, 'v', v, []))
        t += d + 0.055
    return ev, t


def slug(text):
    return ''.join(ch if ch.isalpha() else '_' for ch in text.lower()).strip('_')[:24]


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def b64wav(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    adir = os.path.join(here, 'audio', '8bit')
    if not os.path.isdir(adir):
        os.makedirs(adir)

    cards = []

    # 00 · 芬兰语八元音色板
    ev, t = vowel_events(FIN_SHOWCASE)
    data = render(ev, base.Inton('fall', 'slow', t))
    p_vow = os.path.join(adir, '00_demo_vowels.wav')
    write_wav8(p_vow, data)
    print('OK 00_demo_vowels.wav (%.1fs)' % t)
    cards.append(
        '<div class="card demo">'
        '<div class="row"><span class="num">00 · 芬兰语元音色板</span>'
        '<span class="tag">a ä e i o ö u y · 长短各一遍</span></div>'
        '<div class="spell">a ä e i o ö u y / aa ää ee ii oo öö uu yy</div>'
        '<div class="meta">本语言取 a e i o u 五枚；ä ö y 为芬兰语蓝本的另外三枚</div>'
        '<audio controls preload="none" src="data:audio/wav;base64,%s"></audio></div>'
        % b64wav(p_vow))

    # 00b · 音素示范（与神经声线同款内容）
    ev, t = base.build_events(PHONEME_DEMO, 'slow')
    data = render(ev, base.Inton('fall', 'slow', t))
    p_demo = os.path.join(adir, '00_demo.wav')
    write_wav8(p_demo, data)
    print('OK 00_demo.wav (%.1fs)' % t)
    cards.append(
        '<div class="card demo">'
        '<div class="row"><span class="num">00b · 音素示范</span>'
        '<span class="tag">a aa e ee i ii o oo u uu · 全辅音</span></div>'
        '<div class="spell">%s</div>'
        '<div class="meta">aa ee ii oo uu · p t th k kh ts s sh h m n l r j w · ai au ei ou</div>'
        '<audio controls preload="none" src="data:audio/wav;base64,%s"></audio></div>'
        % (esc(PHONEME_DEMO), b64wav(p_demo)))

    # 14 句例句（与 tts.py 同款语料）
    for num, scene, text, vc in TTS_PHRASES:
        mode = MODES.get(num, 'normal')
        kind = 'chant' if mode == 'chant' else ('rise' if text.rstrip().endswith('?') else 'fall')
        ev, total = base.build_events(text, mode)
        inton = base.Inton(kind, mode, total)
        data = render(ev, inton, mode == 'whisper')
        name = '%02d_%s.wav' % (num, slug(text))
        write_wav8(os.path.join(adir, name), data)
        spell, ipa, gloss = META[num]
        cards.append(
            '<div class="card">'
            '<div class="row"><span class="num">%02d · %s</span>'
            '<span class="tag">%s</span></div>'
            '<div class="spell">%s</div>'
            '<div class="meta">%s</div>'
            '<div class="gloss">%s</div>'
            '<audio controls preload="none" src="data:audio/wav;base64,%s"></audio></div>'
            % (num, esc(scene), esc(mode), esc(spell), esc(ipa), esc(gloss), b64wav(os.path.join(adir, name))))
        print('OK %s (%.1fs, %s)' % (name, total, mode))

    html = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thelshaat 线语 · 8-BIT 声线</title>
<style>
  body{margin:0;padding:24px 18px 44px;background:#10101a;color:#c8e8c8;
       font:13px/1.7 "Courier New",ui-monospace,Consolas,monospace}
  body::after{content:"";position:fixed;inset:0;pointer-events:none;
       background:repeating-linear-gradient(0deg,rgba(0,0,0,.14) 0 1px,transparent 1px 3px)}
  .wrap{max-width:1080px;margin:0 auto}
  h1{font-size:16px;letter-spacing:.1em;color:#7dff7d;margin:0 0 4px;text-shadow:0 0 10px rgba(80,255,120,.5)}
  .sub{font-size:11px;color:#6f8f6f;margin:0 0 6px}
  .specs{font-size:11px;color:#5a7a5a;margin:0 0 16px;padding:8px 12px;
         border:2px dashed #2e4a2e;border-radius:6px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}
  .card{background:#161624;border:2px solid #2e4a2e;border-radius:6px;padding:12px 14px}
  .card.demo{border-color:#59c959}
  .row{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
  .num{font-size:12px;color:#59c959;letter-spacing:.05em}
  .tag,.meta,.gloss{font-size:11px;color:#6f8f6f}
  .spell{font-size:15px;margin-top:4px;color:#eaffea}
  audio{width:100%;height:32px;margin-top:8px}
</style></head><body><div class="wrap">
<h1>THELSHAAT · 8-BIT 声线</h1>
<p class="sub">/ˈtʰɛl.ʃaːt/ · 芬兰语元音蓝本 · 与神经声线同款 14 句 + 音素示范</p>
<div class="specs">SR 11025Hz / 8-bit PCM · NES APU 脉冲波 25% 占空比（鼻/边 50%，闪音 12.5%）·
LFSR 噪声声道 · 音高锁 12 平均律半音格 · 共振峰台阶 40/80/160Hz · 幅度 16 级 · 控制 60Hz 帧 ·
重音恒在第一音节（音系规则 v1.0 §4）· 第 09 句为耳语（纯噪声声道）</div>
<div class="grid">__CARDS__</div>
</div></body></html>""".replace('__CARDS__', ''.join(cards))

    with open(os.path.join(here, 'player8.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    print('OK player8.html')


if __name__ == '__main__':
    main()
