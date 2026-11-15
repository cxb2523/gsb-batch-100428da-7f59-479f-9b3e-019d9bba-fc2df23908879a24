"""
Multi-format report generator for jsrip scan results.
"""

import json
import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


class ReportGenerator:
    """Generate reports in multiple formats (JSON, Markdown, HTML, PDF, CSV)."""
    
    def __init__(self, output_dir, logger):
        """
        Initialize the report generator.
        
        Args:
            output_dir: Output directory path
            logger: Logger instance
        """
        self.output_dir = Path(output_dir)
        self.logger = logger
        self.reports_dir = self.output_dir / 'reports'
        self.reports_dir.mkdir(parents=True, exist_ok=True)
    
    def generate_report(self, results, format_type):
        """
        Generate a report in the specified format.
        
        Args:
            results: Scan results dictionary
            format_type: Report format ('json', 'md', 'html', 'pdf', 'csv')
        """
        self.logger.info(f"Generating {format_type.upper()} report...")
        
        try:
            if format_type == 'json':
                self._generate_json(results)
            elif format_type == 'md':
                self._generate_markdown(results)
            elif format_type == 'html':
                self._generate_html(results)
            elif format_type == 'pdf':
                self._generate_pdf(results)
            elif format_type == 'csv':
                self._generate_csv(results)
            elif format_type == 'intel':
                self._generate_intel_md(results)
            else:
                self.logger.warning(f"Unknown report format: {format_type}")
        except Exception as e:
            self.logger.error(f"Error generating {format_type} report: {e}")
    
    def _generate_json(self, results):
        """Generate JSON report."""
        output_file = self.reports_dir / 'report.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        self.logger.info(f"JSON report: {output_file}")
    
    def _generate_markdown(self, results):
        """Generate Markdown report."""
        output_file = self.reports_dir / 'report.md'
        
        with open(output_file, 'w', encoding='utf-8') as f:
            # Header
            f.write("# jsrip Analysis Report\n\n")
            f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # Scan Info
            scan_info = results.get('scan_info', {})
            f.write("## Scan Information\n\n")
            f.write(f"- **Start Time:** {scan_info.get('start_time', 'N/A')}\n")
            f.write(f"- **End Time:** {scan_info.get('end_time', 'N/A')}\n")
            f.write(f"- **Duration:** {scan_info.get('duration', 'N/A')}\n")
            f.write(f"- **URLs Analyzed:** {scan_info.get('urls_count', 0)}\n\n")
            
            # Statistics
            stats = results.get('statistics', {})
            f.write("## Statistics\n\n")
            # Fall back to the findings themselves so a renamed statistics key
            # can never silently turn these into zeros.
            _f = results.get('findings', {})
            js_count = stats.get('js_files_found', len(_f.get('js_files', [])))
            ep_list = _f.get('endpoints', [])
            ep_count = stats.get('endpoints_found', len(ep_list))
            sec_count = stats.get('secrets_found', len(_f.get('secrets', [])))
            domains = {urlparse(e.get('url', '')).hostname
                       for e in ep_list if e.get('url', '').startswith('http')}
            domains.discard(None)

            f.write(f"- **JavaScript Files:** {js_count}\n")
            f.write(f"- **Secrets Found:** {sec_count}\n")
            f.write(f"- **Endpoints Discovered:** {ep_count}\n")
            f.write(f"- **Unique Domains:** {len(domains)}\n\n")
            
            # Secrets
            secrets = results.get('findings', {}).get('secrets', [])
            if secrets:
                f.write("## Secrets Found\n\n")
                f.write("| Type | Value | File | Confidence | Entropy |\n")
                f.write("|------|-------|------|------------|----------|\n")
                
                for secret in secrets:
                    value = secret.get('value', '')
                    # Truncate long values
                    if len(value) > 50:
                        value = value[:47] + '...'
                    
                    f.write(f"| {secret.get('type', 'N/A')} | "
                           f"`{value}` | "
                           f"{secret.get('file', 'N/A')} | "
                           f"{secret.get('confidence', 'N/A')} | "
                           f"{secret.get('entropy', 'N/A')} |\n")
                
                f.write("\n")
                
                # Secret details
                f.write("### Secret Details\n\n")
                for i, secret in enumerate(secrets, 1):
                    f.write(f"#### {i}. {secret.get('type', 'Unknown')}\n\n")
                    f.write(f"- **File:** `{secret.get('file', 'N/A')}`\n")
                    f.write(f"- **Value:** `{secret.get('value', 'N/A')}`\n")
                    f.write(f"- **Confidence:** {secret.get('confidence', 'N/A')}\n")
                    f.write(f"- **Entropy:** {secret.get('entropy', 'N/A')}\n")
                    
                    if 'also_in' in secret:
                        f.write(f"- **Also found in:** {', '.join(secret['also_in'])}\n")
                    
                    context = secret.get('context', '')
                    if context:
                        f.write(f"\n**Context:**\n```javascript\n{context}\n```\n")
                    f.write("\n")
            else:
                f.write("## Secrets Found\n\nNo secrets detected.\n\n")
            
            # Endpoints
            endpoints = results.get('findings', {}).get('endpoints', [])
            if endpoints:
                f.write("## Endpoints Discovered\n\n")
                for endpoint in endpoints:
                    f.write(f"- `{endpoint.get('url', 'N/A')}` ")
                    f.write(f"(from: {endpoint.get('source_file', 'N/A')})\n")
                f.write("\n")
            else:
                f.write("## Endpoints Discovered\n\nNo endpoints found.\n\n")
            
            # JS Files
            js_files = results.get('findings', {}).get('js_files', [])
            if js_files:
                f.write("## JavaScript Files\n\n")
                f.write("| Filename | URL | Size | SHA256 |\n")
                f.write("|----------|-----|------|--------|\n")
                
                for js_file in js_files:
                    f.write(f"| {js_file.get('filename', 'N/A')} | "
                           f"{js_file.get('url', 'N/A')} | "
                           f"{js_file.get('size', 0)} bytes | "
                           f"`{js_file.get('sha256', 'N/A')[:16]}...` |\n")
                f.write("\n")
        
        self.logger.info(f"Markdown report: {output_file}")
    
    def _generate_html(self, results):
        """Generate a modern, interactive HTML report."""
        import html as html_mod
        output_file = self.reports_dir / 'report.html'

        scan_info = results.get('scan_info', {})
        stats = results.get('statistics', {})
        secrets = results.get('findings', {}).get('secrets', [])
        endpoints = results.get('findings', {}).get('endpoints', [])
        js_files = results.get('findings', {}).get('js_files', [])
        subdomain_recon = results.get('subdomain_recon', {})
        total_subs = sum(d.get('total', 0) for d in subdomain_recon.values())
        alive_subs = sum(d.get('alive', 0) for d in subdomain_recon.values())

        js_count = stats.get('js_files_found', stats.get('js_files_downloaded', len(js_files)))
        secrets_count = stats.get('secrets_found', len(secrets))
        endpoints_count = stats.get('endpoints_found', stats.get('endpoints_discovered', len(endpoints)))
        duration = scan_info.get('duration', 'N/A')
        gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        target_urls = scan_info.get('urls', [])

        high_c = sum(1 for s in secrets if s.get('confidence') == 'high')
        med_c = sum(1 for s in secrets if s.get('confidence') == 'medium')
        low_c = sum(1 for s in secrets if s.get('confidence') == 'low')
        info_c = sum(1 for s in secrets if s.get('confidence') == 'info')

        def esc(v):
            return html_mod.escape(str(v)) if v else ''

        def size_fmt(b):
            b = int(b) if b else 0
            if b >= 1048576: return f"{b/1048576:.1f} MB"
            if b >= 1024: return f"{b/1024:.1f} KB"
            return f"{b} B"

        # Build secrets JSON for JS interactivity
        secrets_json_items = []
        for i, s in enumerate(secrets):
            val = s.get('value', '')
            ctx = s.get('context', '')
            secrets_json_items.append(
                '{' +
                f'"i":{i},' +
                f'"type":{json.dumps(s.get("type",""))},' +
                f'"value":{json.dumps(val)},' +
                f'"file":{json.dumps(s.get("file",""))},' +
                f'"confidence":{json.dumps(s.get("confidence","info"))},' +
                f'"entropy":{json.dumps(s.get("entropy",0))},' +
                f'"category":{json.dumps(s.get("category","generic"))},' +
                f'"source":{json.dumps(s.get("source","pattern_match"))},' +
                f'"context":{json.dumps(ctx[:400])}' +
                '}'
            )
        secrets_json = '[' + ','.join(secrets_json_items) + ']'

        # Build endpoints JSON
        ep_json_items = []
        for ep in endpoints:
            ep_json_items.append(
                '{' +
                f'"url":{json.dumps(ep.get("url",""))},' +
                f'"file":{json.dumps(ep.get("file", ep.get("source_file","")))}' +
                '}'
            )
        ep_json = '[' + ','.join(ep_json_items) + ']'

        # Build JS files JSON
        jf_json_items = []
        for jf in js_files:
            jf_json_items.append(
                '{' +
                f'"filename":{json.dumps(jf.get("filename",""))},' +
                f'"url":{json.dumps(jf.get("url",""))},' +
                f'"size":{json.dumps(jf.get("size",0))},' +
                f'"sha256":{json.dumps(jf.get("sha256",""))},' +
                f'"source_page":{json.dumps(jf.get("source_page",""))}' +
                '}'
            )
        jf_json = '[' + ','.join(jf_json_items) + ']'

        # Build subdomains JSON
        sub_json_items = []
        for domain, ddata in subdomain_recon.items():
            for r in ddata.get('results', []):
                sub_json_items.append(
                    '{' +
                    f'"subdomain":{json.dumps(r.get("subdomain",""))},' +
                    f'"ip":{json.dumps(r.get("ip",""))},' +
                    f'"status_code":{json.dumps(r.get("status_code",0))},' +
                    f'"alive":{json.dumps(r.get("alive",False))},' +
                    f'"source":{json.dumps(r.get("source",""))},' +
                    f'"domain":{json.dumps(domain)}' +
                    '}'
                )
        sub_json = '[' + ','.join(sub_json_items) + ']'

        html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>jsrip // {esc(', '.join(target_urls[:2]))}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;font-size:13px;line-height:1.7;color:#1a1a1a;background:#f4f1ec;padding:40px 20px}}
.wrap{{max-width:960px;margin:0 auto}}
a{{color:#1a1a1a}}
hr{{border:none;border-top:1px solid #c8c3b8;margin:28px 0}}
h1{{font-size:15px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;margin-bottom:4px}}
h2{{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;border-bottom:2px solid #1a1a1a;padding-bottom:6px;margin:0 0 16px 0}}
.mono{{font-family:inherit}}

/* Header */
.hdr{{border:2px solid #1a1a1a;padding:24px 28px;margin-bottom:28px;position:relative}}
.hdr::after{{content:'JSRIP';position:absolute;top:12px;right:16px;font-size:32px;font-weight:900;color:rgba(0,0,0,.06);letter-spacing:.05em}}
.hdr .meta{{color:#666;font-size:12px;margin-top:8px}}
.hdr .meta span{{margin-right:20px}}

/* Nav */
.nav{{display:flex;gap:4px;margin-bottom:28px;flex-wrap:wrap}}
.nav a{{padding:4px 12px;border:1px solid #c8c3b8;color:#1a1a1a;text-decoration:none;font-size:11px;text-transform:uppercase;letter-spacing:.06em}}
.nav a:hover{{background:#1a1a1a;color:#f4f1ec;border-color:#1a1a1a}}

/* Summary strip */
.strip{{display:flex;gap:0;margin-bottom:28px;border:1px solid #c8c3b8}}
.strip div{{flex:1;padding:12px 16px;border-right:1px solid #c8c3b8;text-align:center}}
.strip div:last-child{{border-right:none}}
.strip .n{{font-size:22px;font-weight:700;display:block}}
.strip .l{{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#888}}

/* Severity stamps */
.stamp{{display:inline-block;padding:1px 6px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;border:2px solid;line-height:1.6}}
.stamp.high{{color:#b91c1c;border-color:#b91c1c}}.stamp.medium{{color:#92400e;border-color:#92400e}}
.stamp.low{{color:#1e40af;border-color:#1e40af}}.stamp.info{{color:#888;border-color:#aaa}}
.stamp.off{{opacity:.25;cursor:pointer}}.stamp.on{{cursor:pointer}}

/* Search */
.sbar{{margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
.sbar input{{padding:5px 10px;border:1px solid #c8c3b8;background:transparent;font:inherit;font-size:12px;flex:1;min-width:180px;outline:none}}
.sbar input:focus{{border-color:#1a1a1a}}
.act{{padding:4px 10px;border:1px solid #c8c3b8;background:transparent;font:inherit;font-size:11px;cursor:pointer;text-transform:uppercase;letter-spacing:.04em}}
.act:hover{{background:#1a1a1a;color:#f4f1ec}}

/* Table */
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:6px 10px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#888;border-bottom:2px solid #1a1a1a;cursor:pointer;user-select:none;white-space:nowrap}}
th:hover{{color:#1a1a1a}}
td{{padding:6px 10px;border-bottom:1px solid #ddd8d0;font-size:12px;vertical-align:top}}
tr.click{{cursor:pointer}}tr.click:hover{{background:rgba(0,0,0,.03)}}

/* Finding detail */
.det{{display:none;border-left:3px solid #b91c1c;padding:12px 16px;margin:4px 0 12px 0;background:#eee9e0}}
.det.open{{display:block}}
.det .k{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#888;margin-bottom:2px}}
.det .v{{word-break:break-all;margin-bottom:10px;position:relative;padding:6px 8px;background:#f4f1ec;border:1px solid #ddd8d0}}
.det .ctx{{background:#1a1a1a;color:#d4d0c8;padding:10px 12px;overflow-x:auto;white-space:pre-wrap;word-break:break-all;line-height:1.6;font-size:11px}}
.det .row{{display:flex;gap:20px;margin-bottom:10px;flex-wrap:wrap}}
.det .row div{{font-size:11px}}

/* Copy */
.cp{{position:absolute;top:4px;right:4px;padding:2px 8px;border:1px solid #c8c3b8;background:#f4f1ec;font:inherit;font-size:10px;cursor:pointer;text-transform:uppercase;letter-spacing:.04em}}
.cp:hover{{background:#1a1a1a;color:#f4f1ec}}.cp.ok{{border-color:#166534;color:#166534}}

/* Entropy */
.ent{{display:inline-flex;align-items:center;gap:4px}}
.ent .bar{{width:40px;height:4px;background:#ddd8d0;display:inline-block}}
.ent .bar span{{display:block;height:100%}}

/* Category */
.cat{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;padding:1px 5px;border:1px solid #c8c3b8}}

/* Endpoint */
.ep{{word-break:break-all}}

/* Section */
.sec{{margin-bottom:32px}}

/* Filter row */
.frow{{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap;align-items:center}}
.frow .lbl{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#888;margin-right:4px}}

/* Empty */
.empty{{padding:20px;text-align:center;color:#999;font-size:12px}}

/* Footer */
.ftr{{text-align:center;padding:20px 0;color:#aaa;font-size:11px;letter-spacing:.04em}}

/* Responsive */
@media(max-width:600px){{.strip{{flex-wrap:wrap}}.strip div{{flex:1 1 45%;border-bottom:1px solid #c8c3b8}}}}
@media print{{body{{background:#fff;padding:20px}}.nav,.sbar,.act,.cp,.frow{{display:none!important}}.det{{display:block!important;border-left-color:#000}}}}
</style>
</head>
<body>
<div class="wrap">

<div class="hdr" id="top">
  <h1>jsrip analysis report</h1>
  <div class="meta">
    <span>DATE: {esc(gen_time)}</span>
    <span>DURATION: {esc(duration)}</span>
    <span>TARGETS: {len(target_urls)}</span>
  </div>
  <div class="meta" style="margin-top:4px">{esc(', '.join(target_urls[:6]))}</div>
</div>

<div class="nav">
  {'<a href="#subdomains">Subdomains (' + str(total_subs) + ')</a>' if subdomain_recon else ''}
  <a href="#findings">Findings ({secrets_count})</a>
  <a href="#endpoints">Endpoints ({endpoints_count})</a>
  <a href="#assets">Assets ({js_count})</a>
  <a href="#top">Top</a>
</div>

<div class="strip">
  {'<div><span class="n">' + str(total_subs) + '</span><span class="l">Subdomains</span></div><div><span class="n">' + str(alive_subs) + '</span><span class="l">Alive</span></div>' if subdomain_recon else ''}
  <div><span class="n">{js_count}</span><span class="l">JS files</span></div>
  <div><span class="n">{secrets_count}</span><span class="l">Secrets</span></div>
  <div><span class="n">{endpoints_count}</span><span class="l">Endpoints</span></div>
  <div><span class="n">{esc(duration)}</span><span class="l">Duration</span></div>
</div>

{'<hr><div class="sec" id="subdomains"><h2>Subdomains</h2><div class="sbar"><input type="text" id="sdq" placeholder="filter..." oninput="rSD()"></div><div class="frow"><span class="lbl">Show:</span><span class="stamp high on" data-sd="alive" onclick="tglSD(this)">ALIVE (' + str(alive_subs) + ')</span><span class="stamp info on" data-sd="dead" onclick="tglSD(this)">DEAD (' + str(total_subs - alive_subs) + ')</span></div><table><thead><tr><th onclick="sSD(\'subdomain\')">subdomain</th><th onclick="sSD(\'ip\')">ip</th><th onclick="sSD(\'status_code\')">status</th><th onclick="sSD(\'source\')">source</th><th onclick="sSD(\'domain\')">domain</th></tr></thead><tbody id="sdb"></tbody></table></div>' if subdomain_recon else ''}

<hr>

<div class="sec" id="findings">
  <h2>Findings</h2>
  <div class="frow">
    <span class="lbl">Filter:</span>
    <span class="stamp high on" data-c="high" onclick="tgl(this)">HIGH ({high_c})</span>
    <span class="stamp medium on" data-c="medium" onclick="tgl(this)">MED ({med_c})</span>
    <span class="stamp low on" data-c="low" onclick="tgl(this)">LOW ({low_c})</span>
    <span class="stamp info on" data-c="info" onclick="tgl(this)">INFO ({info_c})</span>
  </div>
  <div class="sbar">
    <input type="text" id="sq" placeholder="filter..." oninput="rS()">
    <button class="act" onclick="xpA()">reveal all</button>
    <button class="act" onclick="clA()">collapse</button>
  </div>
  <table id="st"><thead><tr>
    <th onclick="sS('type')">#  type</th>
    <th onclick="sS('file')">file</th>
    <th onclick="sS('confidence')">sev</th>
    <th onclick="sS('entropy')">entropy</th>
    <th onclick="sS('category')">cat</th>
  </tr></thead><tbody id="sb"></tbody></table>
  <div id="ns" class="empty" style="display:none">Nothing here.</div>
</div>

<hr>

<div class="sec" id="endpoints">
  <h2>Endpoints</h2>
  <div class="sbar"><input type="text" id="eq" placeholder="filter..." oninput="rE()"></div>
  <table><thead><tr><th onclick="sE('url')">url</th><th onclick="sE('file')">source</th></tr></thead><tbody id="eb"></tbody></table>
  <div id="ne" class="empty" style="display:none">Nothing here.</div>
</div>

<hr>

<div class="sec" id="assets">
  <h2>JS Assets</h2>
  <div class="sbar"><input type="text" id="jq" placeholder="filter..." oninput="rJ()"></div>
  <table><thead><tr>
    <th onclick="sJ('filename')">file</th>
    <th onclick="sJ('size')">size</th>
    <th>sha256</th>
    <th>origin</th>
  </tr></thead><tbody id="jb"></tbody></table>
</div>

<hr>
<div class="ftr">jsrip &mdash; {esc(gen_time)}</div>

</div>

<script>
const S={secrets_json};
const E={ep_json};
const J={jf_json};
const SD={sub_json};
let sdf={{alive:1,dead:1}};
let sds={{k:'alive',a:0}};
function tglSD(el){{let c=el.dataset.sd;sdf[c]=sdf[c]?0:1;el.classList.toggle('on');el.classList.toggle('off');rSD()}}
function sSD(k){{if(sds.k===k)sds.a=!sds.a;else{{sds.k=k;sds.a=1}};rSD()}}
function rSD(){{
  let q=(document.getElementById('sdq')||{{}}).value;
  if(q===undefined)return;
  q=(q||'').toLowerCase();
  let f=SD.filter(s=>{{
    if(!sdf[s.alive?'alive':'dead'])return false;
    if(q&&!((s.subdomain+s.ip+s.source+s.domain).toLowerCase().includes(q)))return false;
    return true;
  }});
  let k=sds.k;
  f.sort((a,b)=>{{let av=k==='status_code'?+a[k]:k==='alive'?(a[k]?0:1):String(a[k]||'').toLowerCase();let bv=k==='status_code'?+b[k]:k==='alive'?(b[k]?0:1):String(b[k]||'').toLowerCase();if(av<bv)return sds.a?-1:1;if(av>bv)return sds.a?1:-1;return 0}});
  let h='';
  for(let s of f){{
    let cls=s.alive?'high':'info';
    h+=`<tr><td>${{esc(s.subdomain)}}</td><td>${{esc(s.ip)}}</td><td><span class="stamp ${{cls}}">${{s.alive?s.status_code:'DEAD'}}</span></td><td><span class="cat">${{esc(s.source)}}</span></td><td>${{esc(s.domain)}}</td></tr>`;
  }}
  document.getElementById('sdb').innerHTML=h;
}}
let cf={{high:1,medium:1,low:1,info:1}};
let ss={{k:'confidence',a:1}},es={{k:'url',a:1}},js={{k:'filename',a:1}};
const co={{high:0,medium:1,low:2,info:3}};
function esc(s){{let d=document.createElement('div');d.textContent=s;return d.innerHTML}}
function sf(b){{b=+b||0;if(b>=1048576)return(b/1048576).toFixed(1)+'M';if(b>=1024)return(b/1024).toFixed(1)+'K';return b+'B'}}
function ec(e){{if(e>=4)return'#b91c1c';if(e>=3)return'#92400e';if(e>=2)return'#1e40af';return'#aaa'}}
function ep(e){{return Math.min(100,Math.max(0,(e/6)*100))}}
function cp(t,b){{navigator.clipboard.writeText(t).then(()=>{{b.textContent='OK';b.classList.add('ok');setTimeout(()=>{{b.textContent='COPY';b.classList.remove('ok')}},1200)}})}}
function tgl(el){{let c=el.dataset.c;cf[c]=cf[c]?0:1;el.classList.toggle('on');el.classList.toggle('off');rS()}}
function xpA(){{document.querySelectorAll('.det').forEach(p=>p.classList.add('open'))}}
function clA(){{document.querySelectorAll('.det').forEach(p=>p.classList.remove('open'))}}
function td(i){{let p=document.getElementById('d'+i);if(p)p.classList.toggle('open')}}
function sS(k){{if(ss.k===k)ss.a=!ss.a;else{{ss.k=k;ss.a=1}};rS()}}
function sE(k){{if(es.k===k)es.a=!es.a;else{{es.k=k;es.a=1}};rE()}}
function sJ(k){{if(js.k===k)js.a=!js.a;else{{js.k=k;js.a=1}};rJ()}}

function rS(){{
  let q=(document.getElementById('sq').value||'').toLowerCase();
  let f=S.filter(s=>{{if(!cf[s.confidence])return false;if(q&&!((s.type+s.file+s.value+s.category).toLowerCase().includes(q)))return false;return true}});
  let k=ss.k;
  f.sort((a,b)=>{{let av=k==='entropy'?+a[k]:k==='confidence'?(co[a[k]]??9):String(a[k]||'').toLowerCase();let bv=k==='entropy'?+b[k]:k==='confidence'?(co[b[k]]??9):String(b[k]||'').toLowerCase();if(av<bv)return ss.a?-1:1;if(av>bv)return ss.a?1:-1;return 0}});
  let h='';
  if(!f.length){{document.getElementById('ns').style.display='block';document.getElementById('st').style.display='none'}}
  else{{document.getElementById('ns').style.display='none';document.getElementById('st').style.display='';
    for(let s of f){{
      let c=s.confidence||'info';
      h+=`<tr class="click" onclick="td(${{s.i}})"><td>${{esc(s.type)}}</td><td>${{esc(s.file)}}</td><td><span class="stamp ${{c}}">${{c.toUpperCase()}}</span></td><td><span class="ent">${{(+s.entropy).toFixed(1)}}<span class="bar"><span style="width:${{ep(s.entropy)}}%;background:${{ec(s.entropy)}}"></span></span></span></td><td><span class="cat">${{esc(s.category||'-')}}</span></td></tr>`;
      h+=`<tr><td colspan="5" style="padding:0"><div class="det" id="d${{s.i}}"><div class="k">value</div><div class="v">${{esc(s.value)}}<button class="cp" onclick="event.stopPropagation();cp(${{JSON.stringify(s.value).replace(/</g,'\\\\u003c')}},this)">COPY</button></div><div class="row"><div><span class="k">source</span><br>${{esc(s.source)}}</div><div><span class="k">category</span><br>${{esc(s.category||'-')}}</div><div><span class="k">entropy</span><br><strong style="color:${{ec(s.entropy)}}">${{(+s.entropy).toFixed(2)}}</strong></div></div>${{s.context?`<div class="k">context</div><div class="ctx">${{esc(s.context)}}</div>`:''}}</div></td></tr>`;
    }}
  }}
  document.getElementById('sb').innerHTML=h;
}}

function rE(){{
  let q=(document.getElementById('eq').value||'').toLowerCase();
  let f=E.filter(e=>{{if(q&&!((e.url+e.file).toLowerCase().includes(q)))return false;return true}});
  let k=es.k;f.sort((a,b)=>{{let av=String(a[k]||'').toLowerCase(),bv=String(b[k]||'').toLowerCase();if(av<bv)return es.a?-1:1;if(av>bv)return es.a?1:-1;return 0}});
  if(!f.length)document.getElementById('ne').style.display='block';else document.getElementById('ne').style.display='none';
  let h='';for(let e of f){{let u=esc(e.url);h+=`<tr><td class="ep">${{e.url.startsWith('http')?`<a href="${{u}}" target="_blank">${{u}}</a>`:u}}</td><td>${{esc(e.file)}}</td></tr>`}}
  document.getElementById('eb').innerHTML=h;
}}

function rJ(){{
  let q=(document.getElementById('jq').value||'').toLowerCase();
  let f=J.filter(x=>{{if(q&&!((x.filename+x.url+x.sha256).toLowerCase().includes(q)))return false;return true}});
  let k=js.k;f.sort((a,b)=>{{let av=k==='size'?+a[k]:String(a[k]||'').toLowerCase();let bv=k==='size'?+b[k]:String(b[k]||'').toLowerCase();if(av<bv)return js.a?-1:1;if(av>bv)return js.a?1:-1;return 0}});
  let h='';for(let x of f){{let sha=esc(x.sha256||'');h+=`<tr><td>${{esc(x.filename)}}</td><td>${{sf(x.size)}}</td><td style="position:relative">${{sha.substring(0,16)}}...<button class="cp" onclick="cp('${{sha}}',this)" style="position:static;margin-left:6px">COPY</button></td><td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{esc(x.source_page||'')}}">${{esc(x.source_page||'')}}</td></tr>`}}
  document.getElementById('jb').innerHTML=h;
}}

document.addEventListener('DOMContentLoaded',()=>{{rS();rE();rJ();rSD()}});
</script>
</body>
</html>'''

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)

        self.logger.info(f"HTML report: {output_file}")
    
    def _generate_pdf(self, results):
        """Generate PDF report using markdown2pdf conversion."""
        try:
            # Render both source documents up front: pandoc and markdown-pdf
            # consume report.md, weasyprint consumes report.html. Generating
            # only markdown left the weasyprint path with no input, so it
            # silently produced nothing on a fresh `-f pdf` run.
            self._generate_markdown(results)
            self._generate_html(results)

            # Try to convert to PDF using various methods
            md_file = self.reports_dir / 'report.md'
            pdf_file = self.reports_dir / 'report.pdf'
            
            # Method 1: Try using markdown-pdf (if installed)
            try:
                import subprocess
                subprocess.run(
                    ['markdown-pdf', str(md_file), '-o', str(pdf_file)],
                    check=True,
                    capture_output=True
                )
                self.logger.info(f"PDF report: {pdf_file}")
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
            
            # Method 2: Try using pandoc (if installed)
            try:
                import subprocess
                subprocess.run(
                    ['pandoc', str(md_file), '-o', str(pdf_file)],
                    check=True,
                    capture_output=True
                )
                self.logger.info(f"PDF report: {pdf_file}")
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass
            
            # Method 3: Try using weasyprint (Python library)
            try:
                from weasyprint import HTML
                html_file = self.reports_dir / 'report.html'
                if html_file.exists():
                    HTML(filename=str(html_file)).write_pdf(str(pdf_file))
                    self.logger.info(f"PDF report: {pdf_file}")
                    return
            except ImportError:
                pass
            
            # If all methods fail
            self.logger.warning(
                "PDF generation requires one of: markdown-pdf, pandoc, or weasyprint. "
                "Markdown and HTML reports have been generated instead."
            )
            
        except Exception as e:
            self.logger.error(f"Error generating PDF report: {e}")
    
    # (filename, header row, finding key, row builder)
    CSV_REPORTS = (
        ('secrets.csv',
         ['Type', 'Value', 'File', 'Confidence', 'Entropy', 'Context'],
         'secrets',
         lambda s: [s.get('type', ''), s.get('value', ''), s.get('file', ''),
                    s.get('confidence', ''), s.get('entropy', ''),
                    s.get('context', '')[:500]]),
        ('endpoints.csv',
         ['URL', 'Source File'],
         'endpoints',
         lambda e: [e.get('url', ''), e.get('source_file', '')]),
        ('js_files.csv',
         ['Filename', 'URL', 'Size', 'SHA256', 'Source Page'],
         'js_files',
         lambda j: [j.get('filename', ''), j.get('url', ''), j.get('size', ''),
                    j.get('sha256', ''), j.get('source_page', '')]),
    )

    def _generate_csv(self, results):
        """Generate CSV reports for secrets, endpoints and JS files.

        Every file is written even when its section is empty. A clean scan is
        a real result, and anything reading these paths downstream cannot tell
        a missing file apart from a failed run.
        """
        try:
            findings = results.get('findings', {})
            for filename, header, key, to_row in self.CSV_REPORTS:
                rows = findings.get(key) or []
                out_file = self.reports_dir / filename
                with open(out_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(header)
                    writer.writerows(to_row(item) for item in rows)
                self.logger.info(f"{filename}: {len(rows)} rows -> {out_file}")

        except Exception as e:
            self.logger.error(f"Error generating CSV reports: {e}")

    def _generate_intel_md(self, results):
        """Write INTEL.md summary alongside reports/."""
        from collections import defaultdict

        intel = results.get("findings", {}).get("intel")
        if not intel:
            return

        target = (results.get("scan_info", {}).get("urls") or [""])[0]
        ts = results.get("scan_info", {}).get("end_time", "")
        app_files = intel.get("app_files", [])
        sinks = intel.get("sinks", [])
        gates = intel.get("gates", [])
        flags = intel.get("flags", [])
        pm = intel.get("postmessage_no_origin", [])
        eps = intel.get("endpoints_enriched", [])

        out_path = self.output_dir / "INTEL.md"

        if not app_files:
            out_path.write_text(
                f"# JS Intel - {target}\n\n{ts}. No app-tagged files; intel pass skipped.\n",
                encoding="utf-8",
            )
            self.logger.info(f"INTEL report: {out_path}")
            return

        lines = [f"# JS Intel - {target}", ""]
        lines.append(f"{ts} - {len(app_files)} app files inspected.")
        lines.append("")

        lines.append("## App files")
        for f in app_files:
            lines.append(f"- `{f['filename']}` ({f.get('size', 0)} bytes) - {f.get('url', '')}")
        lines.append("")

        if sinks:
            by_label = defaultdict(list)
            for s in sinks:
                by_label[s["label"]].append(s)
            lines.append(f"## Dangerous sinks ({len(sinks)})")
            for label, items in by_label.items():
                lines.append(f"### {label} ({len(items)})")
                for s in items:
                    lines.append(f"- `{s['file']}:{s['line']}`  `{s['snippet']}`")
            lines.append("")

        if gates:
            lines.append(f"## Client-side auth/role gates ({len(gates)})")
            for g in gates:
                lines.append(f"- `{g['file']}:{g['line']}`  `{g['snippet']}`")
            lines.append("")

        if flags:
            lines.append(f"## Debug / feature flags ({len(flags)})")
            for fl in flags:
                lines.append(f"- `{fl['file']}:{fl['line']}`  `{fl['snippet']}`")
            lines.append("")

        if pm:
            lines.append(f"## postMessage without origin check ({len(pm)})")
            for p in pm:
                lines.append(f"- `{p['file']}` - {p['note']}")
            lines.append("")

        state_changing = [e for e in eps if e.get("state_changing")]
        if state_changing:
            lines.append(f"## State-changing endpoints ({len(state_changing)})")
            for e in state_changing:
                lines.append(f"- {e.get('verb', '?')} `{e['url']}`  ({e['file']}) - {e.get('auth_context', 'unknown')}")
            lines.append("")

        authed = [e for e in eps if e.get("auth_context") == "likely_authed" and not e.get("state_changing")]
        if authed:
            lines.append(f"## Auth-likely endpoints ({len(authed)})")
            for e in authed:
                lines.append(f"- `{e['url']}`  ({e['file']})")
            lines.append("")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        self.logger.info(f"INTEL report: {out_path}")