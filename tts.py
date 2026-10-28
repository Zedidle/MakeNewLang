# -*- coding: utf-8 -*-
"""Thelshaat 线语神经语音合成 v4（edge-tts，多声线）。

五套声线，按"与音系设计的同构度"挑，不按好听挑：

  fi 芬兰语（主）   lengas：长短元音为真音位、重音恒第一音节、h 实读、j=[j]
  id 印尼语          h 实读、sy=[ʃ]、词尾塞音不除阻（冷硬收尾）
  es 西班牙语        纯五元音 + 闪音 r + 不送气 p/t/k，最接近设计里的音值
  fr 法语            ch=[ʃ]、y=[j] 完美，但 h 静音（最大的硬伤）、r 为小舌音
  sn 精灵语（模拟）  用威尔士语声线，托尔金设计辛达林的直接底稿：â ê î ô û 真长音、
                     th=[θ]、ch=[x]、h 实读、r 为颤音

用法:
    python tts.py               # 全部五套
    python tts.py fi es         # 只生成指定几套
    python tts.py --rebuild     # 强制重跑已存在的音频

输出:
    audio/<key>/00_demo.mp3 ... 13 句 + 音素示范
    player.html                 多声线切换播放页（音频 base64 内嵌）
"""
import asyncio
import base64
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon_gen import parse_syllables, CLUSTERS

# ---------------------------------------------------------------- 映射表

V_IDENT = {v: v for v in ('a', 'aa', 'e', 'ee', 'i', 'ii', 'o', 'oo', 'u', 'uu',
                          'ai', 'ei', 'au', 'ou', 'ia', 'ua')}

ALL_ONSETS = ['', 'p', 't', 'th', 'k', 'kh', 'ts', 's', 'sh', 'h',
              'm', 'n', 'ng', 'l', 'r', 'j', 'w',
              'st', 'sk', 'sl', 'kr', 'pl']


def on_map(**kw):
    d = {c: c for c in ALL_ONSETS}
    d.update(kw)
    return d


def coda_map(**kw):
    d = {'k': 'k', 't': 't', 'th': 't', 's': 's', 'n': 'n', 'l': 'l', 'm': 'm'}
    d.update(kw)
    return d


# ---- fi 芬兰语：th→t（R0 词尾清化本就作 [t]）、kh→k、sh→š
FI = {
    'label': 'fi 芬兰语',
    'kind': '真实语言',
    'voice': 'fi-FI-NooraNeural',
    'on': on_map(th='t', kh='k', sh='š'),
    'v': V_IDENT,
    'coda': coda_map(th='t'),
    'note': '长短元音为真音位、重音恒在第一音节、h 实读、j=[j]，与音系规则逐条同构；'
            '代价是 kh/th 送气对立归并，sh 写作 š。',
    'demo': 'aa ee ii oo uu, paa taa kaa tsaa saa šaa haa maa naa laa raa jaa vaa, '
            'ai au ei ou, tel haal paat',
    'demo_gloss': 'a aa e ee i ii o oo u uu · p t th k kh ts s sh h m n l r j w',
}

# ---- id 印尼语：sh→sy、j→y（印尼语 j 是 [dʒ]）、kh 保留读 [x]
ID = {
    'label': 'id 印尼语',
    'kind': '真实语言',
    'voice': 'id-ID-GadisNeural',
    'on': on_map(sh='sy', j='y'),
    'v': V_IDENT,
    'coda': coda_map(th='t'),
    'note': 'h 实读、sy=[ʃ]、词尾塞音不除阻（"冷硬"收尾的来源）、kh 读 [x] 保得住送气对立；'
            '代价是重音偏倒数第二音节，与"重音恒第一音节"冲突。',
    'demo': 'aa ee ii oo uu, pa ta ka tsa sa sya ha ma na la ra ya wa, '
            'ai au ei ou, tel haal paat',
    'demo_gloss': 'a aa e ee i ii o oo u uu · p t th k kh ts s sh h m n l r j w',
}

# ---- es 西班牙语：h→j（读 [x]，法语/西语里唯一还能出声的"h"）、sh 并入 s、
#      j→y、w→u；p t k 本就是不送气，与设计音值一致
ES = {
    'label': 'es 西班牙语',
    'kind': '真实语言',
    'voice': 'es-ES-ElviraNeural',
    'on': on_map(th='t', kh='k', sh='s', h='j', j='y', w='u'),
    'v': V_IDENT,
    'coda': coda_map(th='t'),
    'note': '纯五元音、闪音 r [ɾ]、不送气 p/t/k 都与设计音值一致；'
            'h 只能写作 j 读 [x]（西班牙语 h 静音），sh 并入 s，kh 并入 k。',
    'demo': 'a aa e ee i ii o oo u uu, pa ta ka tsa sa ja ma na la ra ya ua, '
            'ai au ei ou, tel jaal paat',
    'demo_gloss': 'a aa e ee i ii o oo u uu · p t th k kh ts s sh h m n l r j w',
}

# ---- fr 法语：u→ou（法语 u 是 [y]）、e→è、长音用 â ê î ô û、sh→ch、
#      j→y、w→ou；词尾 t/s/n/m 需借不发音的 e 才会读出来
FR = {
    'label': 'fr 法语',
    'kind': '真实语言',
    'voice': 'fr-FR-DeniseNeural',
    'on': on_map(th='t', kh='k', sh='ch', h='h', j='y', w='ou'),
    'v': {'a': 'a', 'aa': 'â', 'e': 'è', 'ee': 'ê', 'i': 'i', 'ii': 'î',
          'o': 'o', 'oo': 'ô', 'u': 'ou', 'uu': 'oû',
          'ai': 'aï', 'ei': 'eï', 'au': 'aou', 'ou': 'aou', 'ia': 'ia', 'ua': 'oua'},
    'coda': coda_map(th='t', s='ss', n='nn', m='mm'),
    'coda_final': coda_map(th='te', t='te', s='sse', n='nne', m='me'),
    's_medial': 'ss',        # 元音间的 s 要双写，否则法语读成 [z]
    'note': 'ch=[ʃ]、y=[j] 完美，长音写作 â ê î ô û；但法语 h 静音——'
            '这门语言的"气声开关"会整个丢掉，r 是小舌音 [ʁ] 而非闪音。这是法语方案最大的硬伤。',
    'demo': 'a â è ê i î o ô ou oû, pa ta ka tsa sa cha ma na la ra ya oua, '
            'aï aou eï, tele jâle paate',
    'demo_gloss': 'a aa e ee i ii o oo u uu · p t th k kh ts s sh h m n l r j w',
}

# ---- sn 精灵语（用威尔士语模拟辛达林）：th→th [θ]、kh→ch [x]、k→c、j→i、
#      ua→wa、长音 â ê î ô û、重音在倒数第二音节
SN = {
    'label': 'sn 精灵语（威尔士）',
    'kind': '模拟 · 无真实 TTS',
    'voice': 'cy-GB-NiaNeural',
    'on': on_map(th='th', kh='ch', k='c', j='i', w='w', sk='sc', kr='cr', pl='pl', sh='s'),
    'v': {'a': 'a', 'aa': 'â', 'e': 'e', 'ee': 'ê', 'i': 'i', 'ii': 'î',
          'o': 'o', 'oo': 'ô', 'u': 'w', 'uu': 'ŵ',
          'ai': 'ai', 'ei': 'ei', 'au': 'au', 'ou': 'ow', 'ia': 'ia', 'ua': 'wa'},
    'coda': coda_map(k='c', th='th'),
    'note': '没有精灵语 TTS，这用威尔士语模拟——托尔金设计辛达林时直接照抄的就是威尔士语音系：'
            '纯五元音、â ê î ô û 真长音、th=[θ]（辛达林本音）、ch=[x]、h 实读、r 为颤音。'
            '代价：无 [ʃ]（sh 并入 s）、重音在倒数第二音节。',
    'demo': 'a â e ê i î o ô u û, pa ta tha ca cha tsa sa ha ma na la ra ia wa, '
            'ai au ei ow, tel hâl pât',
    'demo_gloss': 'a aa e ee i ii o oo u uu · p t th k kh ts s sh h m n l r j w',
}

# ---- ja 日语（旧方案，v2 遗留）：拼写按音节映射为片假名
# 辅音行：{辅音: {a,e,i,o,u 的片假名}}（元音顺序固定 a e i o u）
KANA_ROWS = {
    '':   {'a': 'ア', 'e': 'エ', 'i': 'イ', 'o': 'オ', 'u': 'ウ'},
    'p':  {'a': 'パ', 'e': 'ペ', 'i': 'ピ', 'o': 'ポ', 'u': 'プ'},
    't':  {'a': 'タ', 'e': 'テ', 'i': 'ティ', 'o': 'ト', 'u': 'トゥ'},
    'th': {'a': 'タ', 'e': 'テ', 'i': 'ティ', 'o': 'ト', 'u': 'トゥ'},
    'k':  {'a': 'カ', 'e': 'ケ', 'i': 'キ', 'o': 'コ', 'u': 'ク'},
    'kh': {'a': 'カ', 'e': 'ケ', 'i': 'キ', 'o': 'コ', 'u': 'ク'},
    'ts': {'a': 'ツァ', 'e': 'ツェ', 'i': 'ツ', 'o': 'ツォ', 'u': 'ツ'},
    's':  {'a': 'サ', 'e': 'セ', 'i': 'シ', 'o': 'ソ', 'u': 'ス'},
    'sh': {'a': 'シャ', 'e': 'シェ', 'i': 'シ', 'o': 'ショ', 'u': 'シュ'},
    'h':  {'a': 'ハ', 'e': 'ヘ', 'i': 'ヒ', 'o': 'ホ', 'u': 'フ'},
    'm':  {'a': 'マ', 'e': 'メ', 'i': 'ミ', 'o': 'モ', 'u': 'ム'},
    'n':  {'a': 'ナ', 'e': 'ネ', 'i': 'ニ', 'o': 'ノ', 'u': 'ヌ'},
    'ng': {'a': 'ンア', 'e': 'ンエ', 'i': 'ンイ', 'o': 'ンオ', 'u': 'ンウ'},
    'l':  {'a': 'ラ', 'e': 'レ', 'i': 'リ', 'o': 'ロ', 'u': 'ル'},
    'r':  {'a': 'ラ', 'e': 'レ', 'i': 'リ', 'o': 'ロ', 'u': 'ル'},
    'j':  {'a': 'ヤ', 'e': 'イェ', 'i': 'イ', 'o': 'ヨ', 'u': 'ユ'},
    'w':  {'a': 'ワ', 'e': 'ウェ', 'i': 'ウィ', 'o': 'ウォ', 'u': 'ウ'},
}
KANA_APPEND = {'a': 'ア', 'e': 'エ', 'i': 'イ', 'o': 'オ', 'u': 'ウ'}
KANA_CODA = {'n': 'ン', 'm': 'ン', 'k': 'ッ', 't': 'ッ', 'th': 'ッ', 's': 'ス', 'l': 'ル'}
CLUSTER_HEAD = {'s': 'ス', 'k': 'ク', 'p': 'プ'}


def syl_to_kana(onset, v, coda):
    out = ''
    if onset in CLUSTERS:                       # 词首辅音簇：垫一个 u 段
        out += CLUSTER_HEAD[onset[0]]
        onset = onset[1]
    if v in ('a', 'e', 'i', 'o', 'u'):
        out += KANA_ROWS[onset][v]
    elif len(v) == 2 and v[0] == v[1]:          # 长元音 → 长音记号
        out += KANA_ROWS[onset][v[0]] + 'ー'
    else:                                       # 双元音 → 两段拼合
        out += KANA_ROWS[onset][v[0]] + KANA_APPEND[v[1]]
    if coda:
        out += KANA_CODA[coda]
    return out


JA = {
    'label': 'ja 日语',
    'kind': '真实语言',
    'voice': 'ja-JP-NanamiNeural',
    'kana': True,
    # v3 起不再用「、」逐词断句——那正是"不连贯"的根源：每个词都被当成列举项，
    # 整句变成一唱一顿的清单腔。现在和其他声线一样只保留原句标点，让 TTS 自己连读。
    'note': '拼写按音节映射为片假名（长元音→ー、词尾 k/t/th→促音ッ），按原句标点连读。'
            '已知近似：kh/th 送气对立被日语归并，五元音+拍节奏。',
    'demo': 'ア アー エ エー イ イー オ オー ウ ウー、パ タ ティ タ カ カ ツァ サ シャ ハ マ ナ ラ ラ ヤ ワ',
    'demo_gloss': 'a aa e ee i ii o oo u uu · p t th k kh ts s sh h m n l r j w',
}

PROFILES = {'fi': FI, 'id': ID, 'es': ES, 'fr': FR, 'sn': SN, 'ja': JA}

# ---------------------------------------------------------------- 语料

PHRASES = [
    (1, "问候", "Eli shil.", {'rate': '-5%', 'pitch': '+0Hz', 'volume': '+0%'}),
    (2, "询问", "Thi nesha shuma ke?", {'rate': '-5%', 'pitch': '+0Hz', 'volume': '+0%'}),
    (3, "介绍", "Ni sen nel-in het.", {'rate': '-8%', 'pitch': '+0Hz', 'volume': '+0%'}),
    (4, "警告", "Thak! Jek ha!", {'rate': '+8%', 'pitch': '+15Hz', 'volume': '+15%'}),
    (5, "拒绝", "Nel saal mok kum.", {'rate': '-8%', 'pitch': '-10Hz', 'volume': '+0%'}),
    (6, "试探", "Thi sen nel-in melin ke?", {'rate': '-10%', 'pitch': '+0Hz', 'volume': '-5%'}),
    (7, "赞叹", "Laal shil tok.", {'rate': '-5%', 'pitch': '+10Hz', 'volume': '+5%'}),
    (8, "叙事", "Ila thesho-tel.", {'rate': '-12%', 'pitch': '-5Hz', 'volume': '+0%'}),
    (9, "低语", "Nok-in ni luum mokha.", {'rate': '-18%', 'pitch': '-25Hz', 'volume': '-20%'}),
    (10, "号召", "Thel kesa ha!", {'rate': '+5%', 'pitch': '+10Hz', 'volume': '+15%'}),
    (11, "哀伤", "Nel-in haat thet-tel…", {'rate': '-20%', 'pitch': '-15Hz', 'volume': '-8%'}),
    (12, "追问", "Thi na jom-tel ke?", {'rate': '-5%', 'pitch': '+0Hz', 'volume': '+0%'}),
    (13, "谚语", "Thel haal, paath haal.", {'rate': '-30%', 'pitch': '-10Hz', 'volume': '+0%'}),
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

# ---------------------------------------------------------------- 转换


def map_word(word, prof):
    """按音系切分后逐音节映射：声母取 on、元音取 v、韵尾区分词尾/词中。"""
    out = []
    first = True
    parts = word.lower().split('-')
    for pi, part in enumerate(parts):
        syls = parse_syllables(part)
        if syls is None:
            raise ValueError('无法解析词形: %s (部分 %s)' % (word, part))
        for si, (onset, v, coda) in enumerate(syls):
            last = (pi == len(parts) - 1) and (si == len(syls) - 1)
            if prof.get('kana'):                    # ja：整音节出片假名
                out.append(syl_to_kana(onset, v, coda))
                first = False
                continue
            o = prof['on'][onset]
            if onset == 's' and not first and prof.get('s_medial'):
                o = prof['s_medial']
            out.append(o)
            out.append(prof['v'].get(v, v))
            if coda:
                table = prof['coda_final'] if (last and 'coda_final' in prof) else prof['coda']
                out.append(table.get(coda, coda))
            first = False
    return ''.join(out)


TOKEN_RE = re.compile(r'[A-Za-z]+(?:-[A-Za-z]+)*|[^A-Za-z\s]+')


def phrase_to_target(text, prof):
    """逐词映射，标点原位保留（逗号/感叹号/问号决定句子的语调走向）。"""
    parts = []
    for tok in TOKEN_RE.findall(text):
        if tok[0].isalpha():
            if parts:
                parts.append(prof.get('join', ' '))
            parts.append(map_word(tok, prof))
        else:
            parts.append('...' if tok.startswith('…') else tok)
    return ''.join(parts)


async def gen(text, out, voice, vc):
    import edge_tts
    c = edge_tts.Communicate(text, voice,
                             rate=vc.get('rate', '+0%'),
                             pitch=vc.get('pitch', '+0Hz'),
                             volume=vc.get('volume', '+0%'))
    await c.save(out)


def slug(text):
    return ''.join(ch if ch.isalpha() else '_' for ch in text.lower()).strip('_')[:24]


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def build_html(sets):
    buttons, panels, notes = [], [], []
    for idx, (key, prof, entries, demo_b64) in enumerate(sets):
        active = 'active' if idx == 0 else ''
        buttons.append(
            '<button class="tab %s" data-key="%s" onclick="pick(\'%s\')">%s</button>'
            % (active, key, key, esc(prof['label'])))
        cards = [
            '<div class="card demo">'
            '<div class="row"><span class="num">00 · 音素示范</span>'
            '<span class="tag">%s</span></div>'
            '<div class="spell">%s</div>'
            '<div class="meta">%s</div>'
            '<audio controls preload="none" src="data:audio/mpeg;base64,%s"></audio>'
            '</div>' % (esc(prof['voice']), esc(prof['demo']), esc(prof['demo_gloss']), demo_b64)
        ]
        for num, name, scene, spell, ipa, gloss, spoken, b64 in entries:
            cards.append(
                '<div class="card">'
                '<div class="row"><span class="num">%02d · %s</span>'
                '<span class="tag">%s</span></div>'
                '<div class="spell">%s</div>'
                '<div class="meta">%s</div>'
                '<div class="gloss">%s</div>'
                '<div class="spoken">念作 <code>%s</code></div>'
                '<audio controls preload="none" src="data:audio/mpeg;base64,%s"></audio>'
                '</div>' % (num, esc(scene), esc(ipa), esc(spell),
                            esc(gloss), esc(gloss), esc(spoken), b64)
            )
        panels.append(
            '<section class="panel %s" data-key="%s">'
            '<p class="note">%s</p>'
            '<div class="grid">%s</div></section>'
            % (active, key, esc(prof['note']), ''.join(cards)))
        notes.append('<li><b>%s</b>（%s）— %s</li>'
                     % (esc(prof['label']), esc(prof['kind']), esc(prof['note'])))

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thelshaat 线语 · 多声线神经语音</title>
<style>
  :root{--bg:#fbfaf8;--fg:#1c1b19;--dim:#6f6a63;--line:#e6e2dc;--card:#fff;--accent:#8a5a2b;}
  @media (prefers-color-scheme:dark){
    :root{--bg:#141312;--fg:#ece8e2;--dim:#9a948c;--line:#2c2926;--card:#1c1a18;--accent:#d8a06a;}
  }
  *{box-sizing:border-box}
  body{margin:0;padding:24px 20px 40px;background:var(--bg);color:var(--fg);
       font:14px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}
  .wrap{max-width:1120px;margin:0 auto}
  h1{font-size:18px;font-weight:600;margin:0 0 4px;letter-spacing:.01em}
  .sub{color:var(--dim);font-size:12px;margin:0 0 16px}
  .tabs{display:flex;gap:8px;margin:0 0 12px;flex-wrap:wrap}
  .tab{appearance:none;border:1px solid var(--line);background:transparent;color:var(--dim);
       padding:6px 14px;border-radius:999px;font-size:13px;cursor:pointer}
  .tab.active{background:var(--accent);border-color:var(--accent);color:#fff}
  .note{color:var(--dim);font-size:12px;margin:0 0 14px;padding-left:10px;border-left:2px solid var(--line)}
  .panel{display:none}
  .panel.active{display:block}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .card.demo{border-color:var(--accent)}
  .row{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
  .num{font-weight:600;font-size:13px}
  .tag{font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums}
  .spell{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:15px;margin-top:6px}
  .meta{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12px;color:var(--dim);margin-top:2px}
  .gloss{font-size:12px;color:var(--dim);margin-top:4px}
  .spoken{font-size:11px;color:var(--dim);margin-top:4px}
  .spoken code{font-size:11px;background:rgba(127,127,127,.12);padding:1px 5px;border-radius:4px}
  audio{width:100%;height:32px;margin-top:10px}
  .summary{margin:22px 0 0;padding:14px 18px;border:1px solid var(--line);border-radius:12px}
  .summary h2{font-size:13px;font-weight:600;margin:0 0 8px}
  .summary ul{margin:0;padding-left:18px;color:var(--dim);font-size:12px;line-height:1.8}
  .summary b{color:var(--fg);font-weight:500}
</style></head><body><div class="wrap">
<h1>Thelshaat 线语 · 多声线神经语音</h1>
<p class="sub">/ˈtʰɛl.ʃaːt/ · 13 句例句 + 音素示范 · 每套声线按"与音系设计的同构度"挑选</p>
<div class="tabs">__TABS__</div>
__PANELS__
<div class="summary"><h2>五套声线速查</h2><ul>__NOTES__</ul>
<p class="sub" style="margin:10px 0 0">任何真实语言的 TTS 都会带上那门语言的口音——这是用 TTS 造语的固有代价。
精灵语一项没有真实 TTS，是用威尔士语（托尔金设计辛达林的直接底稿）模拟。</p></div>
</div>
<script>
function pick(k){
  document.querySelectorAll('.tab').forEach(function(b){b.classList.toggle('active', b.dataset.key===k)});
  document.querySelectorAll('.panel').forEach(function(p){p.classList.toggle('active', p.dataset.key===k)});
  document.querySelectorAll('audio').forEach(function(a){a.pause()});
}
</script></body></html>""".replace('__TABS__', ''.join(buttons)) \
        .replace('__PANELS__', ''.join(panels)) \
        .replace('__NOTES__', ''.join(notes))


async def build(key, here, rebuild=False):
    prof = PROFILES[key]
    adir = os.path.join(here, 'audio', key)
    if not os.path.isdir(adir):
        os.makedirs(adir)

    jobs, entries = [], []
    for num, scene, text, vc in PHRASES:
        name = '%02d_%s.mp3' % (num, slug(text))
        path = os.path.join(adir, name)
        spoken = phrase_to_target(text, prof)
        if rebuild or not (os.path.exists(path) and os.path.getsize(path) > 2000):
            jobs.append(gen(spoken, path, prof['voice'], vc))
        spell, ipa, gloss = META[num]
        entries.append([num, name, scene, spell, ipa, gloss, spoken, None])
        print('  %02d %s -> %s' % (num, text, spoken))
    if jobs:
        await asyncio.gather(*jobs)

    demo_path = os.path.join(adir, '00_demo.mp3')
    if rebuild or not (os.path.exists(demo_path) and os.path.getsize(demo_path) > 2000):
        await gen(prof['demo'], demo_path, prof['voice'],
                  {'rate': '-22%', 'pitch': '-5Hz', 'volume': '+0%'})

    with open(demo_path, 'rb') as f:
        demo_b64 = base64.b64encode(f.read()).decode('ascii')
    for e in entries:
        with open(os.path.join(adir, e[1]), 'rb') as f:
            e[7] = base64.b64encode(f.read()).decode('ascii')
    print('  OK %s: %d 段' % (key, len(entries) + 1))
    return (key, prof, [tuple(e) for e in entries], demo_b64)


async def main():
    here = os.path.dirname(os.path.abspath(__file__))
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    rebuild = '--rebuild' in sys.argv
    # 指定的声线强制重跑；播放页始终嵌入全部五套（已有的音频直接复用）
    regen = set(k for k in args if k in PROFILES)
    sets = []
    for k in PROFILES:
        print('生成 %s (%s)' % (k, PROFILES[k]['voice']))
        sets.append(await build(k, here, rebuild or k in regen))
    with open(os.path.join(here, 'player.html'), 'w', encoding='utf-8') as f:
        f.write(build_html(sets))
    print('OK player.html')


if __name__ == '__main__':
    asyncio.run(main())
