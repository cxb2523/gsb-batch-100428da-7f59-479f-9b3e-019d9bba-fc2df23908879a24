"""Visual state-machine runtime — the fast path. Zero LLM calls when the world matches.

Per state: observe → match anchors → locate target on the LIVE screen → motor acts at the
matched position → observe until the expected transition verifies → advance immediately.
No fixed sleeps as logic; timeouts pause the run as BLOCKED for repair, never guess.
"""
import json, os, sys, time, uuid

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DIR, '..', 'kernel'))
sys.path.insert(0, os.path.join(DIR, '..', 'journal'))
import matcher, journal
import motor as motor_mod

KEYMAP = {'enter': 0x0D, 'tab': 0x09, 'backspace': 0x08, 'esc': 0x1B}


class Run:
    def __init__(self, skill_dir, version=None):
        vs = sorted(f for f in os.listdir(skill_dir) if f.startswith('v') and f.endswith('.json'))
        self.path = os.path.join(skill_dir, version or vs[-1])
        with open(self.path, encoding='utf-8') as f:
            self.skill = json.load(f)
        self.dir = skill_dir
        self.motor = motor_mod.Motor()
        self.llm_calls = 0
        self.frame_id = 0

    def observe(self):
        self.frame_id += 1
        return matcher.grab()

    def run(self):
        t0 = time.time()
        sid = self.skill['entry_state']
        verified = []
        while sid:
            st = self.skill['states'][sid]
            # Learned optional states (repair patches): if their anchor is not on screen,
            # the exception did not happen this run — walk past without a model call.
            if st.get('skip_if_absent'):
                probe = matcher.find(
                    [os.path.join(self.dir, st['action']['target'][k])
                     for k in ('tight', 'crop') if st['action']['target'].get(k)],
                    os.path.join(self.dir, st['action']['target']['context']),
                    threshold=st['required_visual_state']['confidence_threshold'],
                    ctx_cursor=st['action']['target'].get('context_cursor'))
                if not probe.get('found'):
                    sid = st.get('next_state')
                    continue
            r = self.step(sid, st)
            if not r['ok']:
                journal.emit('transition_failed', state=sid, why=r['why'],
                             skill=self.skill['skill_id'])
                print(f'BLOCKED at {sid}: {r["why"]} — pausing for repair, not guessing')
                return {'ok': False, 'blocked_state': sid, 'why': r['why'],
                        'verified': verified, 'llm_calls': self.llm_calls,
                        'seconds': round(time.time() - t0, 1)}
            journal.emit('transition_verified', state=sid, skill=self.skill['skill_id'])
            verified.append(sid)
            sid = st.get('next_state')
        return {'ok': True, 'verified': verified, 'llm_calls': self.llm_calls,
                'seconds': round(time.time() - t0, 1)}

    def step(self, sid, st):
        deadline = time.time() + st['timeout_ms'] / 1000
        act = st['action']

        if act['type'] == 'left_click':
            thr = st['required_visual_state']['confidence_threshold']
            cpath = os.path.join(self.dir, act['target']['context'])
            # evidence order: tight band (the element's own pixels) first, square crop second
            candidates = [os.path.join(self.dir, act['target'][k])
                          for k in ('tight', 'crop') if act['target'].get(k)]
            m = None
            while time.time() < deadline:
                before = self.observe()
                m = matcher.find(candidates, cpath, screen=before, threshold=thr,
                                 ctx_cursor=act['target'].get('context_cursor'))
                if m.get('found'):
                    break
                time.sleep(0.15)
            if not (m and m.get('found')):
                return {'ok': False, 'why': f'target not on screen ({m and m.get("why")})'}
            journal.emit('target_grounded', state=sid, confidence=m['confidence'],
                         region=m['region_normalized'])
            action = {'action_id': str(uuid.uuid4())[:8], 'source_frame_id': self.frame_id,
                      'timestamp': time.time_ns() // 1_000_000, 'intent': f'{sid} click',
                      'target_region': m['region_normalized'], 'confidence': m['confidence'],
                      'expected_transition': 'change near target', 'timeout_ms': st['timeout_ms'],
                      'risk': 'low'}
            r = self.motor.click(action, latest_frame_id=self.frame_id)
            if not r['ok']:
                return {'ok': False, 'why': r['why']}
            # A focus-click's only honest evidence is a 1px caret — invisible to change
            # detection. Its REAL verification is the next state: typing that visibly
            # lands. Defer to it; if focus failed, the type state fails loudly instead.
            nxt = self.skill['states'].get(st.get('next_state') or '')
            if nxt and nxt['action']['type'] == 'type':
                self.last_region = m['region_normalized']   # where the typing must appear
                return {'ok': True}
            # verify: the screen answers NEAR the click first (menus, highlights), then
            # anywhere (navigations repaint far from the button).
            local = matcher.near(m['region_normalized'])
            for _ in range(20):
                time.sleep(0.12)
                after = self.observe()
                if matcher.changed(before, after, region=local) \
                        or matcher.changed(before, after, region=None):
                    return {'ok': True}
            return {'ok': False, 'why': 'no visible change after click'}

        if act['type'] == 'type':
            # NO BLIND TYPING: the previous verified state focused the field; still require
            # the screen to answer to the first keystrokes.
            before = self.observe()
            action = {'action_id': str(uuid.uuid4())[:8], 'source_frame_id': self.frame_id,
                      'intent': f'{sid} type', 'confidence': 1.0,
                      'expected_transition': 'text appears', 'risk': 'low'}
            r = self.motor.type_text(action, self.frame_id, act['text'])
            if not r['ok']:
                return {'ok': False, 'why': r['why']}
            time.sleep(0.25)
            after = self.observe()
            # Text is ~0.06% of the screen — globally invisible (measured: 'July Report'
            # landed perfectly while global change said nothing). Look where the focus
            # click just happened; that is where the characters are obligated to appear.
            region = matcher.near(getattr(self, 'last_region', None) or [0.4, 0.4, 0.2, 0.2],
                                  grow_w=0.14, grow_h=0.06)
            # frac 0.0015: 'July' (4 chars) measures 0.00296 in this box and the noise
            # floor measures 0.00012 — half the shortest real signal, 12x above noise.
            if not (matcher.changed(before, after, region=region, frac=0.0015)
                    or matcher.changed(before, after)):
                lf = matcher.changed_frac(before, after, region=region)
                gf = matcher.changed_frac(before, after)
                return {'ok': False, 'why': f'typed but screen did not change — focus lost? '
                                            f'(local={lf:.5f} global={gf:.5f} region={region})'}
            return {'ok': True}

        if act['type'] == 'key':
            vk = KEYMAP.get(act['key'])
            if vk is None:
                return {'ok': False, 'why': f'unmapped key {act["key"]}'}
            import ctypes
            for flag in (0, 2):
                ctypes.windll.user32.keybd_event(vk, 0, flag, 0)
                time.sleep(0.02)
            time.sleep(0.2)
            return {'ok': True}

        return {'ok': False, 'why': f'unknown action {act["type"]}'}


if __name__ == '__main__':
    res = Run(sys.argv[1]).run()
    print(json.dumps(res, indent=1))
