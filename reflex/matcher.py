"""Local target matcher — find a recorded target crop on the live screen. No LLM.

Evidence is combined, not trusted from one method: multi-scale grayscale template match
for the target crop, then the CONTEXT crop must also agree (a button that matches but sits
in the wrong neighborhood is a different button). Returns normalized region + confidence.
"""
import ctypes
import json as _json
import os as _os
import time as _time
import cv2
import numpy as np
import mss

# Diagnostic journal for the identical-field mis-route hunt: every find() call writes
# the full evidence chain (context match, concentric match, fallback) so a wrong click
# can be traced to the exact decision that produced it.
_DIAG = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'diag_find.jsonl')


def _diag(rec):
    try:
        with open(_DIAG, 'a', encoding='utf-8') as f:
            rec['ts'] = round(_time.time(), 3)
            f.write(_json.dumps(rec) + '\n')
    except OSError:
        pass

user32 = ctypes.windll.user32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
SM = user32.GetSystemMetrics
VX, VY, VW, VH = SM(76), SM(77), SM(78), SM(79)

SCALES = (1.0, 0.95, 1.05, 0.9, 1.1)
_tmpl_cache = {}


def grab():
    with mss.mss() as sct:
        img = np.array(sct.grab({'left': VX, 'top': VY, 'width': VW, 'height': VH}))[:, :, :3]
    return img


def _load(path):
    t = _tmpl_cache.get(path)
    if t is None:
        t = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        _tmpl_cache[path] = t
    return t


def _match_full(screen_g, tmpl_g):
    """Exhaustive multi-scale — the slow path, kept only as the rare fallback."""
    best = (0.0, None, 1.0)
    for s in SCALES:
        t = cv2.resize(tmpl_g, None, fx=s, fy=s) if s != 1.0 else tmpl_g
        if t.shape[0] >= screen_g.shape[0] or t.shape[1] >= screen_g.shape[1]:
            continue
        r = cv2.matchTemplate(screen_g, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(r)
        if mx > best[0]:
            best = (float(mx), (loc[0], loc[1], t.shape[1], t.shape[0]), s)
    return best


def _match_one(screen_g, tmpl_g, need=0.0, with_second=False):
    """Pyramid search: locate at 1/4 resolution (16x less work), refine full-res in a
    small window. The full-screen 440px-context match went from ~1.5s to ~15ms this way —
    the difference between a benchmark that times out and one that beats the human.
    Falls back to the exhaustive path only when the pyramid can't clear `need`.

    with_second=True refines ALL top peaks (no early break) and returns a 4th element:
    the best confidence at a DIFFERENT row (>8px vertical separation), or None. That
    margin is the twin detector — near-identical siblings score within noise of each
    other, and a win without a gap is a coin flip, not a localization."""
    th, tw = tmpl_g.shape
    if th >= 32 and tw >= 32:
        small_s = cv2.resize(screen_g, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        small_t = cv2.resize(tmpl_g, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        if small_t.shape[0] < small_s.shape[0] and small_t.shape[1] < small_s.shape[1]:
            r = cv2.matchTemplate(small_s, small_t, cv2.TM_CCOEFF_NORMED)
            # Refine the TOP THREE coarse peaks, not the first good-enough one. Three
            # near-identical form rows blur together at 1/4 scale; single-peak early
            # accept sent every field click to the same row (measured: all three texts
            # concatenated in one field). Full-res refinement separates 0.99 from 0.93.
            refined = []
            rr = r.copy()
            for _ in range(3):
                _, cmx, _, cloc = cv2.minMaxLoc(rr)
                if cmx <= 0:
                    break
                # suppress this peak's neighborhood for the next iteration
                sx0, sy0 = max(0, cloc[0] - 4), max(0, cloc[1] - 4)
                rr[sy0:cloc[1] + 5, sx0:cloc[0] + 5] = -1
                px, py = cloc[0] * 4, cloc[1] * 4
                mx0, my0 = max(0, px - tw // 2), max(0, py - th // 2)
                win = screen_g[my0:py + th + th // 2, mx0:px + tw + tw // 2]
                if win.shape[0] <= th or win.shape[1] <= tw:
                    continue
                r2 = cv2.matchTemplate(win, tmpl_g, cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(r2)
                refined.append((float(mx), (loc[0] + mx0, loc[1] + my0, tw, th)))
                if not with_second and refined and max(f[0] for f in refined) >= 0.985:
                    break                          # can't meaningfully beat this
            if refined:
                refined.sort(key=lambda f: -f[0])
                bconf, bbox = refined[0]
                if bconf >= max(need, 0.5):
                    if with_second:
                        second = next(((c, b) for c, b in refined[1:]
                                       if abs(b[1] - bbox[1]) > 8), None)
                        return bconf, bbox, 1.0, second
                    return bconf, bbox, 1.0
    full = _match_full(screen_g, tmpl_g)
    return (*full, None) if with_second else full


def _edges(img_g):
    gx = cv2.Sobel(img_g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(img_g, cv2.CV_32F, 0, 1, ksize=3)
    # Raw float magnitude, deliberately NOT rescaled to 8-bit: per-image max scaling
    # saturates screen and template differently, which is non-affine and caps the
    # correlation at ~0.75 on a true match (measured, run 6 2026-08-01). CCOEFF_NORMED
    # is affine-invariant, so unscaled magnitudes compare cleanly.
    return cv2.magnitude(gx, gy)


def _match_edges(scr_g, tmpl_g, need):
    """Accent-shift rescue. The mock (and real apps re-theming) changes a button's fill
    color between demo and replay; grayscale correlation is only affine-invariant, and a
    3-level image (dark bar / accent fill / white text) whose middle level moves drops to
    0.72-0.83 (measured, runs 1-2 on 2026-08-01). Structure survives where color does not:
    match gradient magnitude instead."""
    return _match_one(_edges(scr_g), _edges(tmpl_g), need=need)


def find(target_png, context_png=None, screen=None, threshold=0.87, ctx_cursor=None):
    """target_png: one path or a list of candidate paths (tried in order, context matched
    ONCE for all of them). Returns {found, confidence, region_normalized}.

    Trust the neighborhood while it still exists. Pixel-identical form fields are only
    distinguishable by their labels — the context crop. But in a shuffled list the
    context's CONTENT no longer exists anywhere, so it cannot be enforced. The context's
    own match confidence says which world we are in (measured: labels intact ≥0.95,
    dissolved neighborhood ≤0.85). CAVEAT (2026-08-01, instrumented runs): a context that
    hangs off the window edge carries desktop background that MOVED at relaunch — an
    intact neighborhood then measures 0.87-0.92, and a hard 0.90 cliff randomly discards
    a correctly-LOCATED context (s5 center was 1.5px off true while conf read 0.8772).
    So the floor for trusting the context's LOCATION is 0.75; the concentric target match
    against `threshold` remains the real acceptance gate either way."""
    scr = screen if screen is not None else grab()
    scr_g = cv2.cvtColor(scr, cv2.COLOR_BGR2GRAY)
    targets = [target_png] if isinstance(target_png, str) else list(target_png)

    # A featureless crop is not a target, it is a coincidence. A blank patch of Notepad's
    # text area matched a blank patch of a BROWSER at confidence 1.0 and the organism typed
    # 33 characters into the wrong window (2026-07-31, measured). Anything with almost no
    # texture is ambiguous by definition and must be refused, not matched.
    for tp in targets:
        t = _load(tp)
        if t is not None and float(np.std(t)) < 8.0:
            return {'found': False, 'why': f'target is featureless (std={float(np.std(t)):.1f}) — '
                                           f'ambiguous, refusing to guess a location'}

    ctx_box = ctx_conf = strip = None
    strip_cur = (0, 0)
    if context_png:
        ctx = _load(context_png)
        if ctx is not None:
            ctx_conf, ctx_box, _ = _match_one(scr_g, ctx, need=0.75)
            # Label strip: the FULL-WIDTH horizontal band of the context through the
            # demo cursor point. Pixel-identical twins (three empty form fields) differ
            # ONLY in the label to their left — and the demo cursor sits at the ENTRY
            # center, ~215px right of the row start, so a ±140 band around the cursor
            # cropped the label out and the twins came back (measured: clean 3-way
            # permutations of field texts, 2026-08-01). Full context width keeps the
            # label in frame no matter where in the row the cursor sat.
            ch, cw = ctx.shape
            ccx, ccy = ctx_cursor if ctx_cursor else (cw // 2, ch // 2)
            sy0 = max(0, ccy - 16)
            s = ctx[sy0:min(ch, ccy + 16), :]
            if s.shape[0] >= 24 and s.shape[1] >= 100 and float(np.std(s)) >= 8.0:
                strip, strip_cur = s, (ccx, ccy - sy0)

    d = {'targets': [_os.path.basename(t) for t in targets],
         'ctx': context_png and _os.path.basename(context_png),
         'threshold': threshold,
         'ctx_conf': ctx_conf and round(ctx_conf, 4), 'ctx_box': ctx_box,
         'tries': []}
    out = {'context_confidence': ctx_conf and round(ctx_conf, 4)}

    # The strip is demo pixels matched globally — it does not depend on the live context
    # match at all, so it is NOT gated on ctx_conf (runs 2/3/5 on 2026-08-01: ctx fell to
    # 0.46-0.74 from off-window background, the gate kept the strip out, and the empty-
    # field misroute returned). Acceptance bar 0.95: every correct strip match measured
    # ≥0.98; the one wrong one (accent-shifted button) measured 0.894.
    if strip is not None:
        sconf, sbox, _, ssecond = _match_one(scr_g, strip, need=max(threshold, 0.95),
                                             with_second=True)
        d['strip_conf'], d['strip_box'] = round(float(sconf), 4), sbox
        d['strip_second'] = ssecond and round(float(ssecond[0]), 4)
        margin_ok = ssecond is None or sconf - ssecond[0] >= 0.02
        if sbox is not None and not margin_ok and ssecond[1] is not None:
            # Similar-label twins ('City' vs 'Code' measured 2 misroutes, 06:08
            # 2026-08-01): whole-strip margin drowns a 4-letter label in 440px of
            # identical pixels. Rematch ONLY the label region (left 42% of the
            # strip) against both candidate rows — the differing fraction is 3-4x
            # denser there. If even labels cannot separate them, this stays
            # ambiguous and the state BLOCKS instead of guessing.
            lab = strip[:, :max(60, int(strip.shape[1] * 0.42))]
            scores = []
            for _, bx in ((sconf, sbox), ssecond):
                y0, x0 = max(0, bx[1] - 4), max(0, bx[0] - 4)
                win = scr_g[y0:bx[1] + lab.shape[0] + 4, x0:bx[0] + lab.shape[1] + 8]
                if win.shape[0] > lab.shape[0] and win.shape[1] > lab.shape[1]:
                    r2 = cv2.matchTemplate(win, lab, cv2.TM_CCOEFF_NORMED)
                    _, mx, _, _ = cv2.minMaxLoc(r2)
                    scores.append(float(mx))
                else:
                    scores.append(0.0)
            d['label_tiebreak'] = [round(s, 4) for s in scores]
            if scores[0] - scores[1] >= 0.025:
                margin_ok = True
            elif scores[1] - scores[0] >= 0.025:
                sconf, sbox = ssecond
                margin_ok = True
        if sbox is not None and sconf >= max(threshold, 0.95) and margin_ok:
            bx = sbox[0] + strip_cur[0] - 60
            by = sbox[1] + strip_cur[1] - 16
            d.update(final_conf=round(float(sconf), 4), final_box=[bx, by, 120, 32],
                     found=True, path='strip')
            _diag(d)
            out['confidence'] = round(float(sconf), 4)
            out.update(found=True,
                       region_normalized=[bx / VW, by / VH, 120 / VW, 32 / VH])
            return out
        if sbox is not None and sconf >= max(threshold, 0.95) and not margin_ok:
            # Twins confirmed and even the label tiebreak cannot separate them. The
            # fallback paths have strictly LESS information than the strip — letting
            # them "resolve" this is how 2 misroutes shipped on 2026-08-01. Pause
            # instead of guess is the whole contract.
            d.update(found=False, path='strip-ambiguous')
            _diag(d)
            out.update(found=False,
                       why=f'ambiguous twins: strip {sconf:.3f} vs '
                           f'{ssecond[0]:.3f}, label tiebreak inconclusive')
            return out
        if sconf < max(threshold, 0.95):
            # The strip carried label evidence but could not certify a row. If the
            # targets are low-texture (twin-prone), the fallback paths would route by
            # POSITION — that is a guess wearing a 1.0 confidence (rep 24, 06:31:
            # strip 0.86, fallback 1.0, wrong field). Block instead. Textured targets
            # (buttons) keep the fallback chain — accent shifts need it.
            twin_prone = all(
                (t := _load(tp)) is not None and float(np.std(t)) < 15.0
                for tp in targets)
            if twin_prone:
                d.update(found=False, path='strip-weak-twin-block',
                         strip_reject_conf=round(float(sconf), 4))
                _diag(d)
                out.update(found=False,
                           why=f'strip below bar ({sconf:.3f}) and targets are '
                               f'twin-prone - refusing to route by position')
                return out

    best = (0.0, None)
    for tp in targets:
        tmpl = _load(tp)
        if tmpl is None:
            continue
        if ctx_box is not None and ctx_conf >= 0.75:
            # Neighborhood intact — and by construction target and context are CONCENTRIC
            # (both crops centered on the demo cursor). So the target lives at the context's
            # center, within a tolerance too small to reach an identical sibling one row
            # away (48px pitch, measured). Searching the whole context box let empty-field
            # twins steal the match.
            tth, ttw = tmpl.shape
            ccx, ccy = ctx_box[0] + ctx_box[2] // 2, ctx_box[1] + ctx_box[3] // 2
            x0 = max(0, ccx - ttw // 2 - 40)
            y0 = max(0, ccy - tth // 2 - 20)
            sub = scr_g[y0:ccy + tth // 2 + 20, x0:ccx + ttw // 2 + 40]
            if sub.shape[0] > tth and sub.shape[1] > ttw:
                r2 = cv2.matchTemplate(sub, tmpl, cv2.TM_CCOEFF_NORMED)
                _, conf, _, loc = cv2.minMaxLoc(r2)
                conf = float(conf)
                box = (loc[0] + x0, loc[1] + y0, ttw, tth)
            else:
                conf, box = 0.0, None
            # Target absent from the context's center = the SIGNATURE of reordered
            # content (a shuffled list keeps its shell, ctx still matches 0.90+, but the
            # item moved — measured 0.64 at center while the row sat rows away). Fall
            # back to finding the distinctive band anywhere on screen.
            t_entry = {'tp': _os.path.basename(tp), 'path': 'concentric',
                       'concentric_conf': round(conf, 4), 'concentric_box': box}
            if conf < threshold:
                gconf, gbox, _ = _match_one(scr_g, tmpl, need=threshold)
                t_entry.update(fallback=True, global_conf=round(float(gconf), 4),
                               global_box=gbox)
                if gbox is not None and gconf > conf:
                    conf, box = gconf, gbox
                if conf < threshold:
                    econf, ebox, _ = _match_edges(scr_g, tmpl, need=threshold)
                    t_entry.update(edge_conf=round(float(econf), 4), edge_box=ebox)
                    if ebox is not None and econf >= threshold and econf > conf:
                        conf, box = econf, ebox
            d['tries'].append(t_entry)
        else:
            conf, box, _ = _match_one(scr_g, tmpl, need=threshold)
            t_entry = {'tp': _os.path.basename(tp), 'path': 'global',
                       'conf': round(float(conf), 4), 'box': box}
            if conf < threshold:
                econf, ebox, _ = _match_edges(scr_g, tmpl, need=threshold)
                t_entry.update(edge_conf=round(float(econf), 4), edge_box=ebox)
                if ebox is not None and econf >= threshold and econf > conf:
                    conf, box = econf, ebox
            d['tries'].append(t_entry)
        if box is not None and conf > best[0]:
            best = (conf, box)
        if best[0] >= threshold:
            break                                   # first candidate that clears wins
    conf, box = best
    out['confidence'] = round(conf, 4)
    d.update(final_conf=round(conf, 4), final_box=box)
    if box is None or conf < threshold:
        d['found'] = False
        _diag(d)
        out.update(found=False, why=f'target below threshold ({conf:.2f})')
        return out
    x, y, w, h = box
    d['found'] = True
    _diag(d)
    out.update(found=True, region_normalized=[x / VW, y / VH, w / VW, h / VH])
    return out


def changed(before, after, region=None, frac=0.003):
    """Changed-pixel-fraction detector. Mean-abs-diff missed a menu dropdown that altered
    0.2% of the screen (measured, first flow.py run) — a real transition the click had
    caused. Counting pixels that moved >20 levels sees small popups; `frac` is the share
    of the inspected area that must have moved. region normalized [x,y,w,h] or None."""
    a, b = before, after
    if region:
        x, y, w, h = [int(v) for v in (region[0] * VW, region[1] * VH, region[2] * VW, region[3] * VH)]
        a = a[max(0, y):y + h, max(0, x):x + w]
        b = b[max(0, y):y + h, max(0, x):x + w]
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return True
    d = cv2.absdiff(a, b)
    return float(np.mean(np.max(d, axis=2) > 20)) > frac


def changed_frac(before, after, region=None):
    """The raw fraction, for diagnostics — same math as changed()."""
    a, b = before, after
    if region:
        x, y, w, h = [int(v) for v in (region[0] * VW, region[1] * VH, region[2] * VW, region[3] * VH)]
        a = a[max(0, y):y + h, max(0, x):x + w]
        b = b[max(0, y):y + h, max(0, x):x + w]
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return -1.0
    return float(np.mean(np.max(cv2.absdiff(a, b), axis=2) > 20))


def near(region, grow_w=0.20, grow_h=0.18):
    """A normalized box around a target where its consequences (menus, highlights, modals)
    are most likely to render."""
    cx, cy = region[0] + region[2] / 2, region[1] + region[3] / 2
    x, y = max(0.0, cx - grow_w), max(0.0, cy - grow_h)
    return [x, y, min(1.0 - x, 2 * grow_w), min(1.0 - y, 2 * grow_h)]
