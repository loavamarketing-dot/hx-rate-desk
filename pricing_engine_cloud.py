"""
HX Pricing Intelligence Engine — Cloud Version
================================================
"""

import os, math
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for, Response, send_from_directory

STATIC_DIR = Path(__file__).parent / "static"

FRED_API_KEY = os.environ.get("FRED_API_KEY", "03c25a0916f5921438a4663770ebafc6")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
API_KEY = os.environ.get("API_KEY", "")  # For automated script access — set in Railway
SECRET_KEY = os.environ.get("SECRET_KEY", "hx-pricing-engine-2026")
TREASURY_2YR = "DGS2"
TREASURY_5YR = "DGS5"
DURATION_ESTIMATE = 2.2
HEDGE_RATIO_5YR = 0.23    # 23% of 5yr passes through to Non-QM pricing
HEDGE_RATIO_2YR = 0.77    # 77% of 2yr passes through to Non-QM pricing
shared_market_inputs = {}  # Shared across all users — set once in the morning

def fetch_fred_rate(series_id):
    try:
        resp = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
            "series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json",
            "sort_order": "desc", "limit": 5,
            "observation_start": (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
        }, timeout=10)
        for obs in resp.json().get("observations", []):
            if obs["value"] != ".":
                return {"date": obs["date"], "rate": float(obs["value"])}
    except Exception as e:
        print(f"[FRED] {e}")
    return None

def get_treasury_snapshot():
    return {"treasury_2yr": fetch_fred_rate(TREASURY_2YR), "treasury_5yr": fetch_fred_rate(TREASURY_5YR), "fetched_at": datetime.now().isoformat()}

def compute_market_impact(si):
    # Use dynamic duration from UI input, fallback to config default
    duration = si.get("duration") or DURATION_ESTIMATE
    spread_factor = duration / 100.0

    d5 = (si.get("current_5yr", 0) - si.get("prev_5yr", 0)) * 100
    d2 = (si.get("current_2yr", 0) - si.get("prev_2yr", 0)) * 100
    ds = si.get("nqm_spread_current", 0) - si.get("nqm_spread_prior", 0)

    price_from_5yr = -(d5 * HEDGE_RATIO_5YR / 100) * duration
    price_from_2yr = -(d2 * HEDGE_RATIO_2YR / 100) * duration
    spi = -(ds * spread_factor)

    tpi = price_from_5yr + price_from_2yr + spi
    blended_rate_bps = (d5 * HEDGE_RATIO_5YR) + (d2 * HEDGE_RATIO_2YR) + ds

    return {
        "delta_5yr_bps": round(d5, 1), "delta_2yr_bps": round(d2, 1), "spread_delta_bps": round(ds, 1),
        "price_from_5yr": round(price_from_5yr, 3),
        "price_from_2yr": round(price_from_2yr, 3),
        "spread_price_impact": round(spi, 3),
        "total_price_impact": round(tpi, 3),
        "blended_rate_delta_bps": round(blended_rate_bps, 1),
        "duration_used": duration,
        "spread_factor_used": round(spread_factor, 4),
    }

app = Flask(__name__)
app.secret_key = SECRET_KEY

def require_auth(f):
    @wraps(f)
    def d(*a,**kw):
        # API key auth for automated scripts (header: X-API-Key)
        if API_KEY and request.headers.get("X-API-Key") == API_KEY:
            return f(*a,**kw)
        # Session auth for browser users
        if APP_PASSWORD and not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*a,**kw)
    return d

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>HX Pricing Intelligence</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{
  --bg:#eceeea;--surface:#ffffff;--surface-2:#e1e4de;--border:#cdd2c8;--border-soft:#dadfd5;
  --ink:#141c17;--ink-dim:#56645a;--ink-faint:#8b968c;
  --accent:#21507a;--accent-soft:#e4ecf1;
  --good:#3f7a52;--good-soft:#e6f0e8;--bad:#a8452f;--bad-soft:#f4e6e1;
  --shadow: 0 1px 2px rgba(20,28,23,.04), 0 12px 32px -16px rgba(20,28,23,.18);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#12181a;--surface:#1a2225;--surface-2:#212b2e;--border:#2e3a3d;--border-soft:#283437;
    --ink:#eef1ee;--ink-dim:#a9b5ac;--ink-faint:#78857c;
    --accent:#7bacd1;--accent-soft:#1f3244;
    --good:#74c48c;--good-soft:#1d3324;--bad:#e2957e;--bad-soft:#3a2620;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 20px 44px -20px rgba(0,0,0,.55);
  }
}
*{box-sizing:border-box;margin:0;padding:0}
html{overflow-x:hidden}
body{background:var(--bg);color:var(--ink);font-family:"Public Sans",system-ui,sans-serif;min-height:100vh;width:100%;display:flex;flex-direction:column;align-items:center;padding:40px 20px 60px;overflow-x:hidden}
.page{width:100%;max-width:520px}
.ticket{background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:var(--shadow);overflow:hidden}
header{padding:22px 26px 18px;border-bottom:1px solid var(--border);display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-start;gap:8px 12px}
.ticket-id{font:600 10px/1 "Public Sans",sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:9px}
h1{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:clamp(21px,6vw,26px);line-height:1.1;letter-spacing:-.01em;text-wrap:balance;margin:0 0 6px}
.sub{font-size:13px;line-height:1.5;color:var(--ink-dim);max-width:36ch}
time#ck{font:500 11px "IBM Plex Mono",monospace;color:var(--ink-faint);white-space:nowrap;padding-top:2px}
section{padding:20px 26px;border-bottom:1px solid var(--border)}
section:last-of-type{border-bottom:none}
.section-label{font:600 10px/1 "Public Sans",sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:14px}
.inputs{display:flex;flex-direction:column;gap:16px}
.group-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:7px}
.group-name{font:500 12.5px "Public Sans",sans-serif;color:var(--ink)}
.group-unit{font:500 11px "IBM Plex Mono",monospace;color:var(--ink-faint)}
.pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px}
.pair.solo{grid-template-columns:1fr}
.field{position:relative;min-width:0}
.field-tag{position:absolute;top:7px;left:10px;font:500 9px/1 "Public Sans",sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-faint);pointer-events:none}
.field input{width:100%;min-width:0;padding:19px 10px 8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--ink);font:600 15px "IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;outline:none;transition:border-color .12s ease,background .12s ease}
.field input:hover{border-color:var(--ink-faint)}
.field input:focus{border-color:var(--accent);background:var(--surface)}
.field.duration input{padding-top:10px}
.field-hint{font:500 10px "IBM Plex Mono",monospace;color:var(--ink-faint);margin-top:4px;overflow-wrap:break-word}
.hint-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;margin-top:-2px}
.actions{margin-top:18px;display:flex;flex-direction:column;gap:0}
.pb{width:100%;padding:11px;background:var(--accent);color:var(--surface);border:none;border-radius:6px;font:600 12.5px "Public Sans",sans-serif;cursor:pointer;letter-spacing:.2px}
.pb:hover{opacity:.92}
.sb{width:100%;padding:10px;background:var(--surface);color:var(--ink-dim);border:1px solid var(--border);border-radius:6px;font:500 12px "Public Sans",sans-serif;cursor:pointer;margin-top:8px}
.sb:hover{background:var(--bg)}
#save-status{font:500 11px "IBM Plex Mono",monospace;color:var(--ink-faint);margin-top:6px;text-align:center;min-height:14px}
.readout{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:var(--border);padding:0}
.stat{background:var(--surface);padding:20px 26px;text-align:left;min-width:0}
.stat-label{font:600 10px/1 "Public Sans",sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:9px}
.stat-value{font:600 clamp(24px,7vw,32px)/1 "IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;letter-spacing:-.01em;transition:color .15s ease}
.stat-value.up{color:var(--good)}
.stat-value.down{color:var(--bad)}
.stat-unit{font:500 11px "IBM Plex Mono",monospace;color:var(--ink-faint);margin-top:5px}
.waterfall{padding:18px 26px 20px}
.wf-row{display:grid;grid-template-columns:1fr auto auto;align-items:baseline;gap:6px 10px;padding:8px 0;border-bottom:1px solid var(--border-soft);font-size:13px}
.wf-row:first-of-type{padding-top:0}
.wf-name{color:var(--ink);min-width:0;overflow-wrap:break-word}
.wf-delta{font:500 12px "IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;color:var(--ink-faint);white-space:nowrap}
.wf-contrib{font:600 13px "IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;min-width:56px;text-align:right;white-space:nowrap}
.wf-contrib.up{color:var(--good)}
.wf-contrib.down{color:var(--bad)}
.wf-contrib.flat{color:var(--ink-faint)}
.wf-row.total{border-bottom:none;margin-top:4px;padding-top:12px;border-top:1.5px solid var(--ink)}
.wf-row.total .wf-name{font-weight:600}
.wf-row.total .wf-contrib{font-size:15px}
.st{margin:0 26px 20px;padding:10px 12px;border-radius:6px;font:500 12.5px "Public Sans",sans-serif}
.st.w{background:var(--bad-soft);color:var(--bad)}
.st.k{background:var(--good-soft);color:var(--good)}
.hd{display:none}
footer{padding:14px 26px 20px;text-align:center}
footer a{font:500 11px "Public Sans",sans-serif;color:var(--ink-faint);text-decoration:none}
footer a:hover{color:var(--accent)}
@media (max-width:480px){
  body{padding:20px 12px 40px}
  header{padding:18px 18px 14px}
  section{padding:16px 18px}
  .st{margin:0 18px 16px}
  .stat{padding:16px 18px}
  .waterfall{padding:14px 18px 16px}
  footer{padding:12px 18px 18px}
  .wf-row{gap:4px 8px;font-size:12px}
  .wf-delta{font-size:11px}
  .wf-contrib{font-size:12px;min-width:48px}
  .wf-row.total .wf-contrib{font-size:14px}
}
</style></head><body>
<div class="page">
<div class="ticket">
<header>
<div>
<div class="ticket-id">HX &middot; Non&#8209;QM Price Ticket</div>
<h1>Price Impact</h1>
<p class="sub">Treasury and spread moves, translated into HX price &amp; rate impact.</p>
</div>
<time id="ck"></time>
</header>

<section class="inputs">
<div class="section-label">Market Inputs</div>

<div class="group">
<div class="group-head"><span class="group-name">5-Year Treasury</span><span class="group-unit">%</span></div>
<div class="pair">
<div class="field"><span class="field-tag">Prior</span><input type="number" id="p5" step="0.01" inputmode="decimal" placeholder="3.95"></div>
<div class="field"><span class="field-tag">Current</span><input type="number" id="c5" step="0.01" inputmode="decimal" placeholder="FRED"></div>
</div>
<div class="hint-row"><span></span><span class="field-hint" id="k5d">&mdash;</span></div>
</div>

<div class="group">
<div class="group-head"><span class="group-name">2-Year Treasury</span><span class="group-unit">%</span></div>
<div class="pair">
<div class="field"><span class="field-tag">Prior</span><input type="number" id="p2" step="0.01" inputmode="decimal" placeholder="4.20"></div>
<div class="field"><span class="field-tag">Current</span><input type="number" id="c2" step="0.01" inputmode="decimal" placeholder="FRED"></div>
</div>
<div class="hint-row"><span></span><span class="field-hint" id="k2d">&mdash;</span></div>
</div>

<div class="group">
<div class="group-head"><span class="group-name">Non&#8209;QM AAA Spread</span><span class="group-unit">bps</span></div>
<div class="pair">
<div class="field"><span class="field-tag">Prior</span><input type="number" id="sp" step="1" inputmode="decimal" placeholder="155"></div>
<div class="field"><span class="field-tag">Current</span><input type="number" id="sc" step="1" inputmode="decimal" placeholder="150"></div>
</div>
</div>

<div class="group duration">
<div class="group-head"><span class="group-name">Duration</span><span class="group-unit">yrs</span></div>
<div class="pair solo">
<div class="field duration"><input type="number" id="dur" step="0.1" value="2.2" inputmode="decimal"></div>
</div>
</div>

<div class="actions">
<button class="pb" onclick="run()">Calculate Impact</button>
<button class="sb" onclick="saveInputs()">Save Inputs for Team</button>
<div id="save-status"></div>
<button class="sb" onclick="ft()">Refresh FRED Rates</button>
</div>
</section>

<div id="st" class="st hd"></div>

<section class="readout" style="padding:0">
<div class="stat">
<div class="stat-label">Price Impact</div>
<div class="stat-value" id="kp">&mdash;</div>
<div class="stat-unit">points</div>
</div>
<div class="stat">
<div class="stat-label">Rate Impact</div>
<div class="stat-value" id="kr">&mdash;</div>
<div class="stat-unit">bps, blended</div>
</div>
</section>

<section class="waterfall">
<div class="section-label">Price Waterfall</div>
<div class="wf-row">
<span class="wf-name">5yr Treasury</span>
<span class="wf-delta" id="d5">&mdash;</span>
<span class="wf-contrib" id="w5">&mdash;</span>
</div>
<div class="wf-row">
<span class="wf-name">2yr Treasury</span>
<span class="wf-delta" id="d2">&mdash;</span>
<span class="wf-contrib" id="w2">&mdash;</span>
</div>
<div class="wf-row">
<span class="wf-name">Non&#8209;QM Spread</span>
<span class="wf-delta" id="ds">&mdash;</span>
<span class="wf-contrib" id="w3">&mdash;</span>
</div>
<div class="wf-row total">
<span class="wf-name">Total impact</span>
<span class="wf-delta"></span>
<span class="wf-contrib" id="wt">&mdash;</span>
</div>
</section>

<footer><a href="/rate-desk">Rate Desk &rarr;</a></footer>
</div>
</div>
<script>
function ck(){document.getElementById('ck').textContent=new Date().toLocaleString('en-US',{weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'})}
setInterval(ck,1000);ck();
function $(id){return document.getElementById(id)}
function pf(id){return parseFloat($(id).value)||0}
function fmtBp(v,d){var s=v>=0?'+':'';return s+v.toFixed(d==null?1:d)+'bp'}
function fmtPts(v){var s=v>=0?'+':'';return s+v.toFixed(3)}
function cls(v){return v>0?'up':(v<0?'down':'flat')}

async function ft(){try{const r=await(await fetch('/api/treasury')).json();
if(r.treasury_5yr){$('k5d').textContent='FRED '+r.treasury_5yr.rate.toFixed(2)+'% · '+r.treasury_5yr.date;$('c5').value=r.treasury_5yr.rate}
if(r.treasury_2yr){$('k2d').textContent='FRED '+r.treasury_2yr.rate.toFixed(2)+'% · '+r.treasury_2yr.date;$('c2').value=r.treasury_2yr.rate}
}catch(e){}}

async function saveInputs(){
const body={prev_5yr:pf('p5'),current_5yr:pf('c5'),prev_2yr:pf('p2'),current_2yr:pf('c2'),nqm_spread_prior:pf('sp'),nqm_spread_current:pf('sc'),duration:pf('dur')};
try{const r=await(await fetch('/api/market_inputs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
$('save-status').textContent='Saved for team at '+r.saved_at;
}catch(e){$('save-status').textContent='Save failed'}}

async function loadSharedInputs(){
try{const r=await(await fetch('/api/market_inputs')).json();
if(r.prev_5yr!=null)$('p5').value=r.prev_5yr;
if(r.current_5yr!=null)$('c5').value=r.current_5yr;
if(r.prev_2yr!=null)$('p2').value=r.prev_2yr;
if(r.current_2yr!=null)$('c2').value=r.current_2yr;
if(r.nqm_spread_prior!=null)$('sp').value=r.nqm_spread_prior;
if(r.nqm_spread_current!=null)$('sc').value=r.nqm_spread_current;
if(r.saved_at)$('save-status').textContent='Team inputs from '+r.saved_at;
}catch(e){}}

async function run(){
const b={prev_5yr:pf('p5'),current_5yr:pf('c5'),prev_2yr:pf('p2'),current_2yr:pf('c2'),nqm_spread_prior:pf('sp'),nqm_spread_current:pf('sc'),duration:pf('dur')};
try{const r=await(await fetch('/api/impact',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json();
if(r.error){ss('w',r.error);return}
$('st').classList.add('hd');
draw(r.market);
}catch(e){ss('w','Failed: '+e.message)}}

function draw(m){
try{
var kp=$('kp');kp.textContent=fmtPts(m.total_price_impact);kp.className='stat-value '+cls(m.total_price_impact);
var kr=$('kr');kr.textContent=fmtBp(m.blended_rate_delta_bps);kr.className='stat-value '+cls(-m.blended_rate_delta_bps);

$('d5').textContent=fmtBp(m.delta_5yr_bps);
$('d2').textContent=fmtBp(m.delta_2yr_bps);
$('ds').textContent=fmtBp(m.spread_delta_bps);

var w5=$('w5');w5.textContent=fmtPts(m.price_from_5yr);w5.className='wf-contrib '+cls(m.price_from_5yr);
var w2=$('w2');w2.textContent=fmtPts(m.price_from_2yr);w2.className='wf-contrib '+cls(m.price_from_2yr);
var w3=$('w3');w3.textContent=fmtPts(m.spread_price_impact);w3.className='wf-contrib '+cls(m.spread_price_impact);
var wt=$('wt');wt.textContent=fmtPts(m.total_price_impact);wt.className='wf-contrib '+cls(m.total_price_impact);
}catch(drawErr){console.error('Draw error:',drawErr);ss('w','Render error: '+drawErr.message);}
}
function ss(t,m){const e=$('st');e.className='st '+t;e.textContent=m;e.classList.remove('hd')}
ft();loadSharedInputs();
</script></body></html>"""

LOGIN_HTML="""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Login</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Public+Sans:wght@400;500;600&display=swap">
<style>
:root{--bg:#eceeea;--surface:#fff;--border:#cdd2c8;--ink:#141c17;--ink-dim:#56645a;--accent:#21507a;--bad:#a8452f;--bad-soft:#f4e6e1;
--shadow:0 1px 2px rgba(20,28,23,.04),0 12px 32px -16px rgba(20,28,23,.18)}
@media(prefers-color-scheme:dark){:root{--bg:#12181a;--surface:#1a2225;--border:#2e3a3d;--ink:#eef1ee;--ink-dim:#a9b5ac;--accent:#7bacd1;--bad:#e2957e;--bad-soft:#3a2620;
--shadow:0 1px 2px rgba(0,0,0,.3),0 20px 44px -20px rgba(0,0,0,.55)}}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:"Public Sans",system-ui,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;padding:20px}
.b{background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:var(--shadow);padding:34px 30px;width:100%;max-width:320px;text-align:center}
.ticket-id{font:600 10px/1 "Public Sans",sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:9px}
h1{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:22px;letter-spacing:-.01em;margin-bottom:20px}
input{width:100%;padding:11px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--ink);font:14px "Public Sans",sans-serif;margin-bottom:12px;outline:none}
input:focus{border-color:var(--accent);background:var(--surface)}
button{width:100%;padding:11px;background:var(--accent);color:var(--surface);font:600 13px "Public Sans",sans-serif;border:none;border-radius:6px;cursor:pointer;letter-spacing:.2px}
button:hover{opacity:.92}
.e{background:var(--bad-soft);color:var(--bad);font:500 12px "Public Sans",sans-serif;padding:8px 10px;border-radius:6px;margin-bottom:12px}
</style></head>
<body><div class="b"><div class="ticket-id">HX &middot; Non&#8209;QM</div><h1>Price Impact</h1>{% if error %}<div class="e">{{error}}</div>{% endif %}<form method="POST"><input type="password" name="password" placeholder="Password" autofocus><button type="submit">Enter</button></form></div></body></html>"""

@app.route("/login",methods=["GET","POST"])
def login():
    if not APP_PASSWORD:return redirect("/")
    if request.method=="POST":
        if request.form.get("password")==APP_PASSWORD:session["authenticated"]=True;return redirect("/")
        return render_template_string(LOGIN_HTML,error="Wrong password")
    return render_template_string(LOGIN_HTML,error=None)

@app.route("/")
@require_auth
def index():return Response(DASHBOARD_HTML, mimetype='text/html')

@app.route("/rate-desk")
@require_auth
def rate_desk():return send_from_directory(STATIC_DIR, "rate_desk.html")

@app.route("/api/treasury")
@require_auth
def api_treasury():return jsonify(get_treasury_snapshot())

@app.route("/api/market_inputs",methods=["GET"])
@require_auth
def api_get_market_inputs():
    return jsonify(shared_market_inputs)

@app.route("/api/market_inputs",methods=["POST"])
@require_auth
def api_save_market_inputs():
    data = request.json
    shared_market_inputs.update({
        "prev_5yr": data.get("prev_5yr"),
        "current_5yr": data.get("current_5yr"),
        "prev_2yr": data.get("prev_2yr"),
        "current_2yr": data.get("current_2yr"),
        "nqm_spread_prior": data.get("nqm_spread_prior"),
        "nqm_spread_current": data.get("nqm_spread_current"),
        "saved_by": data.get("saved_by", "unknown"),
        "saved_at": datetime.now().strftime("%b %d, %I:%M %p"),
    })
    return jsonify({"ok": True, "saved_at": shared_market_inputs["saved_at"]})

@app.route("/api/impact",methods=["POST"])
@require_auth
def api_impact():
    try:
        d=request.json or {}
        si={k:d.get(k,0) for k in["prev_5yr","current_5yr","prev_2yr","current_2yr","nqm_spread_current","nqm_spread_prior","duration"]}
        if not si.get("duration"): si["duration"] = DURATION_ESTIMATE
        mkt = compute_market_impact(si)
        return jsonify({"market":mkt})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[IMPACT ERROR] {tb}")
        return jsonify({"error": f"Impact calculation failed: {str(e)}"})

if __name__=="__main__":
    port=int(os.environ.get("PORT",5050))
    print("="*60);print("  HX Pricing Intelligence Engine");print(f"  http://localhost:{port}");print("="*60)
    app.run(host="0.0.0.0",port=port,debug=True)
