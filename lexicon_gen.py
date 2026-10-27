# -*- coding: utf-8 -*-
"""Thelshaat（线语）词根表生成与音系校验脚本。

用法:
    python lexicon_gen.py

功能:
    1. 校验全部词根是否符合音系规则（音节结构 / 辅音簇 / 词尾 / 元音组合）
    2. 自动生成带 IPA 标注的 lexicon.md
    3. 检查词形唯一性
"""
import io
import os
import sys

CLUSTERS = ['st', 'sk', 'sl', 'kr', 'pl']    # 仅允许出现在词首
DIGRAPHS = ['th', 'kh', 'sh', 'ts', 'ng']    # 单音双字母
DIPHTHONGS = ['ai', 'ei', 'au', 'ou', 'ia', 'ua']
CODAS = set('ktsnlm') | {'th'}               # 词尾允许的辅音
VOWELS = 'aeiou'
CONS = 'ptksmnlrjwh'

ON_IPA = {'p': 'p', 't': 't', 'th': 'tʰ', 'k': 'k', 'kh': 'kʰ', 'ts': 'ts',
          's': 's', 'sh': 'ʃ', 'h': 'h', 'm': 'm', 'n': 'n', 'ng': 'ŋ',
          'l': 'l', 'r': 'ɾ', 'j': 'j', 'w': 'w',
          'st': 'st', 'sk': 'sk', 'sl': 'sl', 'kr': 'kɾ', 'pl': 'pl'}
V_IPA = {'a': 'a', 'e': 'ɛ', 'i': 'i', 'o': 'o', 'u': 'u',
         'aa': 'aː', 'ee': 'ɛː', 'ii': 'iː', 'oo': 'oː', 'uu': 'uː',
         'ai': 'ai', 'ei': 'ei', 'au': 'au', 'ou': 'ou',
         'ia': 'ia', 'ua': 'ua'}
CODA_IPA = {'k': 'k', 't': 't', 'th': 't', 's': 's', 'n': 'n', 'l': 'l', 'm': 'm'}

# 音节切分歧义覆盖表：按设计意图强制切分（默认规则优先把双字母辅音当声母）。
PARSE_OVERRIDES = {
    'thesho': [('th', 'e', ''), ('sh', 'o', '')],   # 出生：the-sho（而非 thes-ho）
    'mokha': [('m', 'o', 'k'), ('h', 'a', '')],     # 无/没有：mok-ha（而非 mo-kha）
}

CATS = [
    ("人称与指示", [
        ("我", "nel"), ("你", "thi"), ("他/她/它", "ko"),
        ("我们", "nelu"), ("你们", "thiu"), ("他们", "kou"),
        ("这", "shi"), ("那", "kho"), ("这里", "ni"), ("那里", "na"),
        ("谁", "ath"), ("什么", "shuma"),
    ]),
    ("数与量", [
        ("一", "tin"), ("二", "se"), ("三", "kon"), ("四", "pa"),
        ("五", "mu"), ("六", "kes"), ("七", "tho"), ("八", "num"),
        ("九", "lanu"), ("十", "sheth"),
        ("多", "poli"), ("少", "itsa"), ("全", "shuum"), ("无/没有", "mokha"),
    ]),
    ("自然", [
        ("天", "uum"), ("云", "hos"), ("雨", "silu"), ("风", "welu"),
        ("雷", "krath"), ("太阳", "ila"), ("月", "shen"), ("星", "tika"),
        ("火", "khak"), ("水", "saal"), ("冰", "skith"), ("雪", "shau"),
        ("海", "muuth"), ("河", "selu"), ("山", "koltu"), ("石", "talu"),
        ("沙", "sam"), ("土", "muth"), ("树", "hastu"), ("叶", "lashu"),
        ("根", "til"), ("花", "mila"), ("草", "pus"), ("果", "tem"),
        ("种子", "teshu"), ("光", "luum"), ("影", "shek"), ("暗", "nokhel"),
    ]),
    ("身体与生物", [
        ("头", "kepu"), ("眼", "shiin"), ("耳", "hinal"), ("口", "pem"),
        ("鼻", "shon"), ("手", "tek"), ("脚", "lum"), ("心", "haat"),
        ("血", "raath"), ("骨", "kos"), ("皮", "puth"), ("毛/发", "shaal"),
        ("声音", "el"), ("呼吸", "hes"),
        ("虫", "kit"), ("蛾", "shos"), ("蛛", "tath"), ("网", "thaan"),
        ("丝/线", "thel"), ("卵", "oth"), ("翅", "plu"), ("尾", "lath"),
        ("壳", "keth"),
    ]),
    ("动作", [
        ("走", "tam"), ("跑", "taram"), ("飞", "pluu"), ("落", "thet"),
        ("停", "sham"), ("来", "oma"), ("去", "jom"), ("说", "kel"),
        ("听", "hin"), ("看", "mot"), ("触", "pet"), ("拿", "kath"),
        ("给", "mil"), ("收", "sem"), ("吃", "kum"), ("喝", "nom"),
        ("咬", "kat"), ("睡", "shelu"), ("醒", "hel"), ("死", "khath"),
        ("活", "hil"), ("出生", "thesho"), ("长大", "krum"), ("杀", "khaal"),
        ("伤", "shet"), ("治", "melath"), ("哭", "noth"), ("笑", "pith"),
        ("唱", "shal"), ("叫", "kera"), ("呼喊", "uuk"), ("找", "sel"),
        ("藏", "hus"), ("追", "tol"), ("逃", "jek"), ("打", "tum"),
        ("断", "teth"), ("织", "kesa"), ("切", "shok"), ("缝", "thiin"),
    ]),
    ("性状", [
        ("大", "kul"), ("小", "pit"), ("高", "thol"), ("低", "tim"),
        ("长", "haal"), ("短", "hut"), ("新", "neshu"), ("旧", "luuth"),
        ("好", "shil"), ("坏", "khoth"), ("冷", "is"), ("热", "khal"),
        ("干", "shei"), ("湿", "sum"), ("快", "kril"), ("慢", "loom"),
        ("强", "khel"), ("弱", "tit"), ("满", "puul"), ("空", "us"),
        ("亮", "lam"), ("重", "khul"), ("轻", "pul"), ("真", "shein"),
        ("假", "mota"), ("美", "milaa"), ("丑", "kotse"), ("干净", "sil"),
        ("危险", "thak"),
    ]),
    ("方位", [
        ("上", "uul"), ("下", "nol"), ("前", "pes"), ("后", "kas"),
        ("内", "nim"), ("外", "eis"), ("远", "haara"), ("近", "tis"),
        ("中", "met"), ("左", "lun"), ("右", "rin"), ("之间", "tena"),
    ]),
    ("时间", [
        ("现在", "nu"), ("以前", "uut"), ("以后", "shoon"), ("日/一天", "pel"),
        ("夜", "nok"), ("早晨", "eli"), ("晚", "shot"), ("年", "haath"),
    ]),
    ("人与社会", [
        ("人", "haan"), ("名字", "nesha"), ("家", "het"), ("门", "ton"),
        ("路", "paath"), ("城", "kolse"), ("王", "khan"), ("神", "ilath"),
        ("敌", "kaath"), ("朋友", "melin"), ("礼物", "pema"),
    ]),
    ("抽象", [
        ("语言", "shaat"), ("歌", "laal"), ("故事", "kataa"), ("梦", "hum"),
        ("命运", "marath"), ("爱", "mii"), ("恨", "khut"), ("怕", "hek"),
        ("希望", "nal"), ("记忆", "hilse"), ("誓言", "sath"), ("规则", "kot"),
        ("错", "tus"), ("答案", "shin"), ("问题", "kesha"), ("秘密", "hisek"),
        ("自由", "loma"), ("力量", "krul"), ("死亡", "thuul"), ("生命", "hilu"),
    ]),
    ("感叹", [
        ("是啊/同意", "i"), ("叹息", "aa"), ("嘘", "shus"),
    ]),
]

FUNCTIONS = [
    ("sen", "是（系词）"), ("mok", "不/否（置于动词前）"), ("tok", "很（程度）"),
    ("ke", "疑问助词（句尾）"), ("ha", "祈使助词"),
    ("-u", "复数后缀"), ("-in", "领属后缀（附于拥有者）"),
    ("-tel", "过去时后缀"), ("-sho", "将来时后缀"),
]


def parse_syllables(word):
    """把拼写切分为音节，返回 [(onset, vowel, coda), ...]，不合法返回 None。"""
    if word in PARSE_OVERRIDES:
        return [tuple(s) for s in PARSE_OVERRIDES[word]]
    syls = []
    i, n = 0, len(word)
    while i < n:
        onset = ''
        if i == 0:
            for c in CLUSTERS:
                if word.startswith(c, i):
                    onset, i = c, i + len(c)
                    break
        if not onset:
            for c in DIGRAPHS:
                if word.startswith(c, i):
                    onset, i = c, i + len(c)
                    break
        if not onset and word[i] in CONS:
            onset, i = word[i], i + 1
        v = ''
        if word[i:i + 2] in DIPHTHONGS:
            v, i = word[i:i + 2], i + 2
        elif i + 1 < n and word[i] in VOWELS and word[i + 1] == word[i]:
            v, i = word[i] * 2, i + 2
        elif word[i] in VOWELS:
            v, i = word[i], i + 1
        else:
            return None
        coda = ''
        if i < n:
            if word.startswith('th', i) and i + 2 == n:
                coda, i = 'th', i + 2
            elif word[i] in CODAS:
                if i + 1 == n:
                    coda, i = word[i], i + 1
                elif word[i + 1] not in VOWELS and i + 2 < n and word[i + 2] in VOWELS:
                    coda, i = word[i], i + 1
                elif word[i + 1] not in VOWELS and i + 2 == n:
                    return None
        syls.append((onset, v, coda))
    return syls


def word_ipa(word):
    syls = parse_syllables(word)
    if syls is None:
        return None
    parts = []
    for onset, v, coda in syls:
        s = ON_IPA.get(onset, onset) + V_IPA.get(v, v)
        if coda:
            s += CODA_IPA.get(coda, coda)
        parts.append(s)
    return '/ˈ' + '.'.join(parts) + '/'


def violations(word):
    probs = []
    if any(ch in 'bdfgvzycqx' for ch in word):
        probs.append('含缺席辅音字母')
    for j in range(len(word) - 1):
        if word[j] == word[j + 1] and word[j] not in VOWELS:
            probs.append('出现双写辅音')
    for c in CLUSTERS:
        pos = word.find(c)
        while pos != -1:
            if pos != 0 and pos + len(c) < len(word) and word[pos + len(c)] not in VOWELS:
                probs.append('辅音簇 %s 出现在非词首' % c)
            pos = word.find(c, pos + 1)
    syls = parse_syllables(word)
    if syls is None:
        probs.append('无法按 (C)(C)V(C) 切分')
    else:
        for k, (onset, _v, _c) in enumerate(syls):
            if onset in CLUSTERS and k != 0:
                probs.append('辅音簇 %s 出现在非词首' % onset)
    return probs


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    all_words = [(g, w, cat) for cat, pairs in CATS for g, w in pairs]

    assert len(all_words) == 200, '词根数量应为 200，实际 %d' % len(all_words)

    seen = {}
    errs = []
    for gloss, word, cat in all_words:
        vs = violations(word)
        if vs:
            errs.append('%s（%s）: %s' % (word, gloss, '；'.join(vs)))
        if word in seen:
            errs.append('重复词形 %s: %s / %s' % (word, seen[word], gloss))
        seen[word] = gloss

    if errs:
        print('发现 %d 个问题:' % len(errs))
        for e in errs:
            print('  - ' + e)
        sys.exit(1)

    lines = []
    lines.append('# Thelshaat 线语 · 词根表')
    lines.append('')
    lines.append('> 共 **200 个词根** + **9 个功能词**。拼写即读音，重音永远在第一音节。')
    lines.append('> IPA 中词尾 /t/ 对应拼写 th（词尾清化，见 phonology.md R0）。')
    lines.append('')
    for cat, pairs in CATS:
        lines.append('## %s（%d）' % (cat, len(pairs)))
        lines.append('')
        lines.append('| 词 | IPA | 词义 |')
        lines.append('|---|---|---|')
        for gloss, word in pairs:
            lines.append('| %s | %s | %s |' % (word, word_ipa(word), gloss))
        lines.append('')
    lines.append('## 功能词（9）')
    lines.append('')
    lines.append('| 词 | IPA | 用法 |')
    lines.append('|---|---|---|')
    for word, usage in FUNCTIONS:
        ipa = word_ipa(word.strip('-')) if word.startswith('-') else word_ipa(word)
        lines.append('| %s | %s | %s |' % (word, ipa, usage))
    lines.append('')

    out = os.path.join(here, 'lexicon.md')
    with io.open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('OK: 200 词根全部通过音系校验，无重复。已生成 lexicon.md')


if __name__ == '__main__':
    main()
