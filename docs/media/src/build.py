#!/usr/bin/env python3
"""Generate the six self-contained HTML sources (en + zh-TW) next to this file."""
import html, os

HERE = os.path.dirname(os.path.abspath(__file__))

CSS = """
:root{--bg:#0E2326;--panel:#091A1D;--line:#2A5057;--text:#EAF2EF;--muted:#8FB0AD;--teal:#5FD0C0;--sand:#F0B35A;--faint:#4F7472}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:transparent}
body{width:%(w)dpx;height:%(h)dpx}
.card{width:%(w)dpx;height:%(h)dpx;background:var(--bg);border:2px solid var(--line);border-radius:36px;color:var(--text);
 font-family:%(sans)s;position:relative;overflow:hidden}
.mono{font-family:Menlo,Monaco,monospace}
"""
SANS_EN = '"Avenir Next","Helvetica Neue","PingFang TC",sans-serif'
SANS_ZH = '"PingFang TC","Avenir Next",sans-serif'

def page(w, h, lang, body, extra):
    sans = SANS_ZH if lang == "zh-TW" else SANS_EN
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><title>workplace-mapping</title><style>%s%s</style></head>'
            '<body><div class="card">%s</div></body></html>') % (lang, CSS % dict(w=w, h=h, sans=sans), extra, body)

T = {
 "en": dict(
  tag="Teach your agent a company's unwritten rules once.",
  sub="A one-page skill and a small CLI. Any chat tool. Any environment.",
  steps=["Someone corrects the agent","One call records it, with its source","Second time: a command enforces it",
         "Every send is checked, approved, sent once, read back","The day ends with a handoff the next session starts from"],
  title="Built to be cheap to run", g1="What the agent reads, in bytes", g2="Calls per task",
  rows=["Skill text loaded in a session","wm next for three items","A check that passes","Record a correction","Send a pre-approved message","End the day"],
  cap="Measured on the project's example, first draft versus now.", first="First draft", now="Now",
  scale="each row on its own scale", smaller="smaller", fewer=lambda n:"%d× fewer"%n, small=lambda r:"%s× smaller"%r),
 "zh-TW": dict(
  tag="把公司沒寫下來的規矩，教你的代理一次就好。",
  sub="一頁技能加一支小指令。任何聊天工具，任何環境。",
  steps=["有人糾正代理","一次呼叫就記下來，附出處","第二次：改由指令來擋","每次送出都先檢查、核准、只送一次、再讀回","收工寫好交接，下一個工作階段從它開始"],
  title="為了省而設計", g1="代理要讀的量（位元組）", g2="每件事要呼叫幾次",
  rows=["工作階段載入的技能本文","三件事的 wm next","檢查通過時","記一筆糾正","送出已授權的訊息","收工"],
  cap="以專案範例實測，第一版對照現在。", first="第一版", now="現在",
  scale="每列各用自己的比例尺", smaller="", fewer=lambda n:"減為 1/%d"%n, small=lambda r:"縮小 %s 倍"%r),
}

def hero(lang):
    t = T[lang]
    body = f'''
<div class="name mono">workplace-mapping</div>
<div class="tag">{html.escape(t["tag"])}</div>
<div class="sub">{html.escape(t["sub"])}</div>
<pre class="term mono"><span class="p">$ wm next</span>
<span class="ok">ok</span>
<span class="do">do  </span>  REQ-15 Moon phase strip on the day view | F1 Flag exists, default off
<span class="ask">ask </span>  REQ-9  CSV export of saved locations    | F2 On for internal accounts
<span class="wait">wait</span>  REQ-12 Tide alerts                      | F3 5%% rollout | on: Okafor</pre>'''.replace("5%%","5%")
    css = '''
.card{padding:54px 60px}
.name{font-size:74px;font-weight:700;letter-spacing:-1px;color:var(--sand);line-height:1}
.tag{font-size:46px;font-weight:600;margin-top:26px;line-height:1.2}
.sub{font-size:27px;color:var(--muted);margin-top:14px;line-height:1.3}
.term{position:absolute;left:60px;right:60px;bottom:54px;background:var(--panel);border:2px solid var(--line);border-radius:20px;
 padding:26px 34px;font-size:25px;line-height:1.62;color:var(--text);white-space:pre}
.p{color:var(--muted)}.ok{color:var(--teal);font-weight:700}.do{color:var(--teal);font-weight:700}.ask{color:var(--sand);font-weight:700}.wait{color:#8FB6E8;font-weight:700}
'''
    return page(1600, 640, lang, body, css)

CMD = [None, 'wm note add "Reply inside the thread" --to card:Mara', "wm rule add", "wm action send", "wm wrapup write → wm brief"]
def loop(lang):
    t = T[lang]
    xs = [60, 580, 1100, 1100, 580]; ys = [80, 80, 80, 490, 490]
    cards = ""
    for i in range(5):
        cmd = f'<div class="cmd mono">{html.escape(CMD[i])}</div>' if CMD[i] else ""
        cards += (f'<div class="step" style="left:{xs[i]}px;top:{ys[i]}px"><div class="n mono">{i+1}</div>'
                  f'<div class="lab">{html.escape(t["steps"][i])}</div>{cmd}</div>')
    ah = 'marker-end="url(#h)"'
    svg = f'''<svg width="1600" height="900" style="position:absolute;left:0;top:0" fill="none" stroke="var(--sand)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">
<defs><marker id="h" markerWidth="10" markerHeight="10" refX="6" refY="5" orient="auto" markerUnits="userSpaceOnUse" viewBox="0 0 10 10"><path d="M1 1 L7 5 L1 9" stroke="var(--sand)" stroke-width="2.5"/></marker></defs>
<path d="M512 245 H568" {ah}/><path d="M1032 245 H1088" {ah}/><path d="M1320 422 V478" {ah}/><path d="M1088 655 H1032" {ah}/>
<path d="M568 655 H310 Q280 655 280 625 V422" {ah}/></svg>'''
    css = '''
.step{position:absolute;width:440px;height:330px;background:var(--panel);border:2px solid var(--line);border-radius:24px;padding:28px 30px}
.n{width:46px;height:46px;border-radius:23px;background:var(--sand);color:var(--bg);font-weight:700;font-size:26px;text-align:center;line-height:46px}
.lab{font-size:31px;font-weight:600;line-height:1.28;margin-top:20px}
.cmd{position:absolute;left:30px;right:30px;bottom:28px;font-size:22px;line-height:1.45;color:var(--teal);word-break:break-word}
'''
    return page(1600, 900, lang, svg + cards, css)

def fmt(n): return f"{n:,}"
G1 = [(25593, 3975), (2517, 516), (958, 3)]
G2 = [(3, 1), (3, 1), (4, 1)]
def savings(lang):
    t = T[lang]; BW = 560
    def row(label, b, n, scale, ratio):
        wb = BW * b / scale; wn = max(BW * n / scale, 5)
        return f'''<div class="row"><div class="rl"><span>{html.escape(label)}</span><b>{html.escape(ratio)}</b></div>
<div class="bar"><i class="b" style="width:{wb:.1f}px"></i><em class="mono">{fmt(b)}</em></div>
<div class="bar"><i class="n" style="width:{wn:.1f}px"></i><em class="mono">{fmt(n)}</em></div></div>'''
    c1 = "".join(row(t["rows"][i], b, n, b, t["small"](("%.1f" % (b/n)) if b/n < 10 else "%d" % round(b/n))) for i, (b, n) in enumerate(G1))
    c2 = "".join(row(t["rows"][3+i], b, n, 4, t["fewer"](b//n)) for i, (b, n) in enumerate(G2))
    body = f'''<div class="top"><h1>{html.escape(t["title"])}</h1>
<div class="leg"><span><i class="b"></i>{html.escape(t["first"])}</span><span><i class="n"></i>{html.escape(t["now"])}</span></div></div>
<div class="cols"><section><h2>{html.escape(t["g1"])}<small>{html.escape(t["scale"])}</small></h2>{c1}</section>
<section><h2>{html.escape(t["g2"])}</h2>{c2}</section></div>
<div class="cap">{html.escape(t["cap"])}</div>'''
    css = '''
.card{padding:46px 64px}
.top{display:flex;justify-content:space-between;align-items:baseline}
h1{font-size:56px;font-weight:700;letter-spacing:-.5px;color:var(--sand)}
.leg{display:flex;gap:34px;font-size:26px;color:var(--muted)}.leg span{display:flex;align-items:center;gap:12px}
.leg i{width:34px;height:16px;border-radius:8px;display:block}
.b{background:var(--faint)}.n{background:var(--teal)}
.cols{display:flex;gap:64px;margin-top:30px}
section{width:704px}
h2{font-size:30px;font-weight:600;padding-bottom:12px;border-bottom:2px solid var(--line);display:flex;justify-content:space-between;align-items:baseline}
h2 small{font-size:22px;font-weight:400;color:var(--muted)}
.row{margin-top:20px}
.rl{display:flex;justify-content:space-between;align-items:baseline;font-size:28px;margin-bottom:10px}
.rl b{font-size:28px;color:var(--sand);font-weight:700}
.bar{display:flex;align-items:center;height:36px;margin-top:6px}
.bar i{display:block;height:28px;border-radius:5px 14px 14px 5px}
.bar em{font-style:normal;font-size:25px;margin-left:14px;color:var(--text)}
.cap{position:absolute;left:64px;bottom:34px;font-size:25px;color:var(--muted)}
'''
    return page(1600, 760, lang, body, css)

for lang, suf in (("en", ""), ("zh-TW", ".zh-TW")):
    for name, fn in (("hero", hero), ("loop", loop), ("savings", savings)):
        open(os.path.join(HERE, f"{name}{suf}.html"), "w", encoding="utf-8").write(fn(lang))
