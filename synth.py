# -*- coding: utf-8 -*-
"""Thelshaat 线语语音合成器 v2（接近人声版）。

相对 v1 的两个核心升级：
  1. 声源换成 Rosenberg/LF 型声门脉冲（平滑开相位曲线 + 抖动 + 微颤 + 气息噪声），
     替换 v1 的单样本冲激串——蜂鸣感的根源。
  2. 共振峰过渡：相邻浊音段之间 30ms 线性内插；双元音整体滑翔；
     送气段（h / kh / th 的 aspiration）使用后续元音的共振峰（协同发音）；
     重读元音带先升后降的音高重音；末音节长元音按句型滑向终点音高。

用法:
    python synth.py

输出:
    audio/01_eli_shil.wav ... 13 句例句
    audio/00_demo.wav     音素表示范
    player.html           自包含播放页（音频已内嵌 base64）

Token 统一结构: [kind, params, dur_seconds, flags]
"""
import base64
import math
import os
import random
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_gen import parse_syllables, CLUSTERS

SR = 22050
DT = 1.0 / SR
random.seed(7)

# ---------------------------------------------------------------- 共振峰表（扁平 6 元组: f1,b1,f2,b2,f3,b3）
VOW = {
    'a': (730, 80, 1090, 90, 2440, 140),
    'e': (530, 80, 1840, 90, 2480, 140),
    'i': (270, 60, 2290, 100, 3010, 150),
    'o': (400, 70, 750, 90, 2600, 150),
    'u': (320, 70, 760, 90, 2400, 150),
}
DIP = {'ai': ('a', 'i'), 'ei': ('e', 'i'), 'au': ('a', 'u'),
       'ou': ('o', 'u'), 'ia': ('i', 'a'), 'ua': ('u', 'a')}
FRIC = {'s': (5800, 1800, 0.40), 'sh': (2900, 1100, 0.45)}
STOPS = {'p': (750, 900, 0), 't': (3900, 1800, 0),
         'th': (3900, 1800, 1), 'k': (1700, 900, 0), 'kh': (1700, 900, 1)}
NAS = {'m': (250, 120, 1100, 200, 2400, 250),
       'n': (250, 120, 1700, 200, 2600, 250),
       'ng': (250, 120, 2200, 220, 2800, 280)}
LIQ = {'l': (360, 70, 1100, 90, 2700, 140),
       'r': (420, 80, 1500, 100, 2500, 150),
       'j': (260, 60, 2300, 100, 3000, 150),
       'w': (300, 70, 690, 90, 2300, 150)}
F4 = (3550, 280)          # 固定第四共振峰，低增益，增加"头腔感"
NEUTRAL = (500, 120, 1500, 120, 2500, 170)

DUR = {'v': 0.13, 'vlong': 0.23, 'dip': 0.17, 'nas': 0.075, 'l': 0.065,
       'r': 0.038, 'glide': 0.055, 'fric': 0.1, 'h': 0.06,
       'closure': 0.05, 'burst': 0.014, 'asp': 0.034, 'aff': 0.065,
       'cstop': 0.045, 'cburst': 0.008, 'pause': 0.09}

TR = 0.03        # 相邻浊音段之间的共振峰过渡时长
OQ = 0.62        # 开相位占周期比例（声门开放商）
TP = 0.42        # 开相位中渐闭段占比（Rosenberg 参数）
F0_TOP = 190.0
F4_GAIN = 0.25
BREATH = 0.04    # 气息噪声混入浊声源的比例


class Res(object):
    """二阶谐振器（共振峰）。系数每 8 个样本更新一次以兼顾性能与平滑。"""
    __slots__ = ('a', 'b', 'c', 'y1', 'y2')

    def __init__(self):
        self.a = self.b = self.c = 0.0
        self.y1 = self.y2 = 0.0

    def set(self, f, bw):
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


def glottal(phase):
    """Rosenberg 声门脉冲：开相位内先加速渐闭，再快速闭合。phase in [0,1)。"""
    if phase < OQ:
        x = phase / OQ
        if x < TP:
            u = x / TP
            return 3 * u * u - 2 * u * u * u
        y = (x - TP) / (1 - TP)
        return 1 - y * y
    return 0.0


def fade(data, at=0.004, rel=0.004):
    n = len(data)
    na = max(1, min(int(at * SR), n))
    nr = max(1, min(int(rel * SR), n))
    for i in range(na):
        data[i] *= i / na
    for i in range(nr):
        data[n - 1 - i] *= i / nr
    return data


def seg_noise(dur, center, bw, amp=0.4):
    """清擦音/爆破/送气：带通噪声（双谐振器）。"""
    n = max(1, int(dur * SR))
    out = [0.0] * n
    r1 = Res(); r1.set(center, bw)
    r2 = Res(); r2.set(center * 0.9, bw * 1.3)
    for i in range(n):
        out[i] = (r1.proc(random.uniform(-1, 1)) +
                  r2.proc(random.uniform(-1, 1))) * 0.5 * amp
    return fade(out)


# ---------------------------------------------------------------- 词法解析
def word_syllables(word):
    syls = []
    for part in word.lower().split('-'):
        s = parse_syllables(part)
        if s is None:
            raise ValueError('无法解析词形: %s (部分 %s)' % (word, part))
        syls.extend(s)
    return syls


def cons_tokens(c, stress):
    if c in STOPS:
        f, bw, asp = STOPS[c]
        t = [['sil', None, DUR['closure'], []],
             ['burst', (f, bw, 0.3), DUR['burst'], []]]
        if asp:
            t.append(['asp', None, DUR['asp'], []])
        return t
    if c in FRIC:
        f, bw, a = FRIC[c]
        return [['fric', (f, bw, a), DUR['fric'] * (1.2 if stress else 1.0), []]]
    if c == 'h':
        return [['h', None, DUR['h'], []]]
    if c == 'ts':
        return [['sil', None, DUR['closure'] * 0.8, []],
                ['burst', (3900, 1800, 0.3), DUR['burst'], []],
                ['fric', (5800, 1800, 0.4), DUR['aff'], []]]
    if c in NAS:
        return [['nas', c, DUR['nas'], []]]
    if c in ('l', 'r', 'j', 'w'):
        kind = 'r' if c == 'r' else ('glide' if c in ('j', 'w') else 'l')
        return [[kind, c, DUR[kind], []]]
    raise ValueError('未知声母: %s' % c)


def coda_tokens(c):
    if c in ('k', 't', 'th'):
        f, bw, _ = STOPS[c]
        return [['sil', None, DUR['cstop'], []],
                ['burst', (f, bw, 0.14), DUR['cburst'], []]]
    if c == 's':
        return [['fric', (5800, 1800, 0.28), 0.055, []]]
    if c in ('m', 'n'):
        return [['nas', c, 0.055, []]]
    if c == 'l':
        return [['l', 'l', 0.055, []]]
    raise ValueError('未知韵尾: %s' % c)


def token_list(word, final_vowel=False):
    toks = []
    syls = word_syllables(word)
    for si, (on, v, coda) in enumerate(syls):
        first = (si == 0)
        last = (si == len(syls) - 1)
        is_stress = first
        fv = final_vowel and last
        if on in CLUSTERS:
            toks += cons_tokens(on[0], is_stress)
            toks += cons_tokens(on[1], is_stress)
        elif on:
            toks += cons_tokens(on, is_stress)
        flags = []
        if is_stress:
            flags.append('stress')
        if fv:
            flags.append('final')
        if v in VOW:
            d = DUR['vlong'] if len(v) == 2 else DUR['v']
            if fv:
                d *= 1.6
            if is_stress:
                d *= 1.3
            toks.append(['v', v, d, flags])
        elif v in DIP:
            d = DUR['dip'] * (1.3 if is_stress else 1.0)
            if fv:
                d *= 1.5
            toks.append(['dip', v, d, flags])
        if coda:
            toks += coda_tokens(coda)
    return toks


# ---------------------------------------------------------------- 语调
class Inton(object):
    def __init__(self, kind, mode, total):
        self.kind = kind
        self.total = max(total, 0.3)
        if mode == 'emph':
            self.top, self.slope = 205.0, 32.0
        elif kind == 'chant':
            self.top, self.slope = 175.0, 14.0
        else:
            self.top, self.slope = 190.0, 42.0

    def f0(self, t):
        return self.top - self.slope * (t / self.total)

    def final_delta(self):
        if self.kind == 'rise':
            return 65.0
        if self.kind == 'chant':
            return -28.0
        return -42.0


# ---------------------------------------------------------------- 事件时间线
def build_events(text, mode):
    rate = {'normal': 1.08, 'emph': 0.9, 'whisper': 1.15,
            'slow': 1.2, 'chant': 1.3}[mode]
    words = [w.strip('!?.,…;:') for w in text.split()]
    words = [w for w in words if w]
    toks = []
    for wi, w in enumerate(words):
        toks += token_list(w, final_vowel=(wi == len(words) - 1))
        if wi < len(words) - 1:
            toks.append(['pause', None, DUR['pause'], []])
    events = []
    t = 0.0
    for tk in toks:
        d = tk[2] * rate
        events.append((t, d, tk[0], tk[1], tk[3]))
        t += d
    return events, t


VOICED = {'v', 'dip', 'nas', 'l', 'r', 'glide'}


def _target_of(kind, params, which):
    """返回浊音段的起始/终止共振峰目标（扁平 6 元组）。"""
    if kind == 'v':
        return VOW[params]
    if kind == 'dip':
        va, vb = DIP[params]
        return VOW[va] if which == 'start' else VOW[vb]
    if kind == 'nas':
        return NAS[params]
    return LIQ[params]  # l / r / glide


def synth_utterance(events, inton, whisper):
    total = events[-1][0] + events[-1][1]
    n_total = int(total * SR) + SR // 50
    buf = [0.0] * n_total

    ne = len(events)
    prev_adj = [None] * ne      # 紧邻的前一个浊音段（终止目标）
    next_adj = [None] * ne      # 紧邻的后一个浊音段（起始目标）
    next_any = [None] * ne      # 之后最近的浊音段（供送气协同发音）
    scan = None
    for i in range(ne - 1, -1, -1):
        kind, params = events[i][2], events[i][3]
        if kind in VOICED:
            scan = _target_of(kind, params, 'start')
        next_any[i] = scan
    for i in range(ne):
        if i > 0 and events[i - 1][2] in VOICED:
            pk, pp = events[i - 1][2], events[i - 1][3]
            prev_adj[i] = _target_of(pk, pp, 'end')
        if i < ne - 1 and events[i + 1][2] in VOICED:
            nk, np_ = events[i + 1][2], events[i + 1][3]
            next_adj[i] = _target_of(nk, np_, 'start')

    BLOCK = 8
    for ei in range(ne):
        start, dur, kind, params, flags = events[ei]
        n = max(1, int(dur * SR))
        base = int(start * SR)
        stress = 'stress' in flags
        final = 'final' in flags

        if kind in VOICED:
            va = _target_of(kind, params, 'start')
            vb = _target_of(kind, params, 'end')
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
            if next_adj[ei] is not None:
                path.append((dur, next_adj[ei]))
            else:
                path.append((dur, vb))

            amp = 1.0
            if kind == 'nas':
                amp = 0.75
            elif kind == 'r':
                amp = 0.55
            elif kind in ('l', 'glide'):
                amp = 0.9
            if stress and kind in ('v', 'dip'):
                amp *= 1.2
            if whisper:
                amp *= 2.1

            f0a = inton.f0(start)
            fdelta = inton.final_delta() if final else 0.0
            rs = [Res() for _ in range(4)]
            y1 = 0.0
            phase = random.uniform(0, 1)
            r4f, r4bw = F4
            rs[3].set(r4f, r4bw)

            for i0 in range(0, n, BLOCK):
                i1 = min(i0 + BLOCK, n)
                tt = (i0 + i1) / 2.0 / n * dur
                cur = _path_at(path, tt)
                for k in range(3):
                    rs[k].set(cur[2 * k], cur[2 * k + 1])
                nb = random.uniform(-1, 1)
                for i in range(i0, i1):
                    x = i / n
                    if whisper:
                        s = random.uniform(-1, 1) * 0.32
                    else:
                        f = f0a * (1 + 0.008 * random.uniform(-1, 1))
                        f *= 1 + 0.005 * math.sin(2 * math.pi * 5.2 * (start + x * dur))
                        if stress and kind in ('v', 'dip'):
                            f += 16 * math.sin(math.pi * x)
                        if final:
                            sm = x * x * (3 - 2 * x)
                            f += fdelta * sm
                        phase += f * DT
                        if phase >= 1:
                            phase -= 1
                        s = glottal(phase)
                        s += nb * BREATH
                    env = 1.0
                    if x < 0.012:
                        env = x / 0.012
                    elif x > 0.96:
                        env = (1 - x) / 0.04
                    y = (rs[0].proc(s) + rs[1].proc(s) + rs[2].proc(s) +
                         F4_GAIN * rs[3].proc(s))
                    outv = y - y1
                    y1 = y
                    buf[base + i] += outv * amp * env
        elif kind == 'fric':
            f, bw, a = params
            piece = seg_noise(dur, f, bw, a)
            for i, v in enumerate(piece):
                buf[base + i] += v
        elif kind in ('h', 'asp'):
            tgt = next_any[ei] or NEUTRAL
            n = max(1, int(dur * SR))
            base = int(start * SR)
            r1 = Res(); r1.set(tgt[0], tgt[1] * 2.2)
            r2 = Res(); r2.set(tgt[2], tgt[3] * 2.2)
            amp = 0.25 if kind == 'asp' else 0.28
            for i in range(n):
                x = random.uniform(-1, 1)
                v = (r1.proc(x) + r2.proc(x)) * 0.5 * amp
                if i < 0.006 * SR:
                    v *= i / (0.006 * SR)
                buf[base + i] += v
        elif kind == 'burst':
            f, bw, a = params
            piece = seg_noise(dur, f, bw, a)
            for i, v in enumerate(piece):
                buf[base + i] += v
        # sil / pause: 缓冲区默认为零

    # 软限幅 + 峰值归一化
    for i in range(n_total):
        x = buf[i]
        buf[i] = math.tanh(1.35 * x) / math.tanh(1.35)
    peak = max(abs(x) for x in buf) or 1.0
    g = 0.8 / peak
    return [x * g for x in buf]


def _path_at(path, t):
    for k in range(len(path) - 1):
        t0, v0 = path[k]
        t1, v1 = path[k + 1]
        if t <= t1:
            if t1 <= t0:
                return v1
            a = (t - t0) / (t1 - t0)
            return tuple(v0[j] + (v1[j] - v0[j]) * a for j in range(6))
    return path[-1][1]


def write_wav(path, data):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b''.join(
            struct.pack('<h', int(max(-1, min(1, x)) * 32767)) for x in data))


PHRASES = [
    (1, "问候", "Eli shil.", "fall", "normal"),
    (2, "询问", "Thi nesha shuma ke?", "rise", "normal"),
    (3, "介绍", "Ni sen nel-in het.", "fall", "normal"),
    (4, "警告", "Thak! Jek ha!", "fall", "emph"),
    (5, "拒绝", "Nel saal mok kum.", "fall", "normal"),
    (6, "试探", "Thi sen nel-in melin ke?", "rise", "normal"),
    (7, "赞叹", "Laal shil tok.", "fall", "normal"),
    (8, "叙事", "Ila thesho-tel.", "fall", "normal"),
    (9, "低语", "Nok-in ni luum mokha.", "fall", "whisper"),
    (10, "号召", "Thel kesa ha!", "fall", "emph"),
    (11, "哀伤", "Nel-in haat thet-tel…", "fall", "slow"),
    (12, "追问", "Thi na jom-tel ke?", "rise", "normal"),
    (13, "谚语", "Thel haal, paath haal.", "chant", "chant"),
]

META = {
    1: ("Eli shil.", "/ˈɛ.li ˈʃil/", "早晨·好"),
    2: ("Thi nesha shuma ke?", "/tʰi ˈnɛ.ʃa ˈʃu.ma kɛ/", "你·名字·什么·疑问"),
    3: ("Ni sen nel-in het.", "/ni sɛn ˈnɛ.lin hɛt/", "这里·是·我的·家"),
    4: ("Thak! Jek ha!", "/tʰak jɛk ha/", "危险·逃·祈使"),
    5: ("Nel saal mok kum.", "/nɛl ˈsaːl mok kum/", "我·水·不·喝"),
    6: ("Thi sen nel-in melin ke?", "/tʰi sɛn ˈnɛ.lin ˈmɛ.lin kɛ/", "你·是·我的·朋友·疑问"),
    7: ("Laal shil tok.", "/ˈlaːl ʃil tok/", "歌·好·很"),
    8: ("Ila thesho-tel.", "/ˈi.la ˈtʰɛ.ʃo.tɛl/", "太阳·出生·过去"),
    9: ("Nok-in ni luum mokha.", "/ˈnok.in ni ˈluːm ˈmok.ha/", "夜·这里·光·没有"),
    10: ("Thel kesa ha!", "/tʰɛl ˈkɛ.sa ha/", "线·织·祈使"),
    11: ("Nel-in haat thet-tel…", "/ˈnɛ.lin ˈhaːt ˈtʰɛt.tɛl/", "我的·心·落·过去"),
    12: ("Thi na jom-tel ke?", "/tʰi na ˈjom.tɛl kɛ/", "你·那里·去·过去·疑问"),
    13: ("Thel haal, paath haal.", "/tʰɛl haːl paːt haːl/", "线·长 路·长"),
}


def slug(text):
    return ''.join(ch if ch.isalpha() else '_' for ch in text.lower()).strip('_')[:24]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    adir = os.path.join(here, 'audio')
    if not os.path.isdir(adir):
        os.makedirs(adir)

    entries = []
    for num, scene, text, kind, mode in PHRASES:
        events, total = build_events(text, mode)
        inton = Inton(kind, mode, total)
        data = synth_utterance(events, inton, mode == 'whisper')
        name = '%02d_%s.wav' % (num, slug(text))
        write_wav(os.path.join(adir, name), data)
        t, ipa, gloss = META[num]
        entries.append((num, name, scene, t, ipa, gloss, mode))
        print('OK %s (%.1fs, %s)' % (name, total, mode))

    demo_words = ('a aa e ee i ii o oo u uu '
                  'pa ta tha ka kha sa sha ha ma na la ra ja wa').split()
    toks = []
    for w in demo_words:
        toks += token_list(w)
        toks.append(['pause', None, 0.24, []])
    events = []
    t = 0.0
    for tk in toks:
        events.append((t, tk[2], tk[0], tk[1], tk[3]))
        t += tk[2]
    inton = Inton('fall', 'normal', t)
    data = synth_utterance(events, inton, False)
    write_wav(os.path.join(adir, '00_demo.wav'), data)
    print('OK 00_demo.wav (%.1fs)' % t)

    cards = []
    with open(os.path.join(adir, '00_demo.wav'), 'rb') as f:
        demo_b64 = base64.b64encode(f.read()).decode('ascii')
    cards.append(
        '<div style="background:var(--color-background-info);border-radius:12px;padding:14px 16px;">'
        '<div style="font-weight:500;font-size:14px;">00 · 音素表示范</div>'
        '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:2px;">'
        'a aa e ee i ii o oo u uu · pa ta tha ka kha · sa sha ha · ma na · la ra ja wa</div>'
        '<audio controls preload="none" style="width:100%;margin-top:8px;height:32px;" '
        'src="data:audio/wav;base64,' + demo_b64 + '"></audio></div>')

    for num, name, scene, t, ipa, gloss, mode in entries:
        with open(os.path.join(adir, name), 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('ascii')
        cards.append(
            '<div style="background:var(--color-background-secondary);border-radius:12px;padding:14px 16px;">'
            '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;">'
            '<span style="font-weight:500;font-size:14px;">%02d · %s</span>'
            '<span style="font-size:11px;color:var(--color-text-tertiary);">%s</span></div>'
            '<div style="font-family:var(--font-mono);font-size:14px;margin-top:4px;">%s</div>'
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:2px;">%s · %s</div>'
            '<audio controls preload="none" style="width:100%%;margin-top:8px;height:32px;" '
            'src="data:audio/wav;base64,%s"></audio></div>'
            % (num, scene, mode, t, ipa, gloss, b64))

    html = ('<h2 class="sr-only">Thelshaat 线语合成语音播放页（v2 接近人声引擎），内嵌 14 段音频</h2>'
            '<h3 style="font-size:14px;font-weight:500;margin:0 0 4px;">Thelshaat 线语 · 合成语音 v2</h3>'
            '<p style="font-size:12px;color:var(--color-text-secondary);margin:0 0 12px;">'
            '引擎升级：Rosenberg/LF 声门脉冲声源 + 相邻浊音段 30ms 共振峰过渡 + 双元音滑翔 + 送气协同发音 + 音高重音与微颤。'
            '重音在第一音节，语调按 phonology.md 规则；第 9 句为耳语气声模式。</p>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;">'
            + ''.join(cards) + '</div>')

    with open(os.path.join(here, 'player.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    print('OK player.html')


if __name__ == '__main__':
    main()
