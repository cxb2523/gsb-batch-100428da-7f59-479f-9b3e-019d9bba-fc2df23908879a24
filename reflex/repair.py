"""Exception repair — the ONLY place Claude is invoked, and the reason the organism evolves.

When a skill blocks, the runtime has already refused to guess. This builds a compact repair
package (state, what was expected, what is actually on screen, recent history), asks Claude
for ONE repair plan, executes it cautiously with the same verification discipline as normal
execution, and — on success — writes the repair into the skill as a new version.

The point: the SAME exception never costs a second LLM call. v1 blocked, v2 knows.
"""
import base64, json, os, shutil, subprocess, sys, time, uuid

DIR = os.path.dirname(os.path.abspath(__file__))
ORG = os.path.abspath(os.path.join(DIR, '..'))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(ORG, 'kernel'))
sys.path.insert(0, os.path.join(ORG, 'journal'))

import cv2
import matcher, journal
import motor as motor_mod

SYSTEM = (
    'You repair a visual workflow that stalled. You see ONE screenshot of the live screen '
    'and the crop of the element the workflow expected but could not find.\n'
    'Answer with ONE JSON object, no prose:\n'
    '{"diagnosis":"<one short sentence>",'
    '"repair_steps":[{"action":"click","description":"<the visible thing to click, quote its '
    'exact on-screen label>"}],'
    '"resume_state":"<state id to resume at>"}\n'
    'Rules: use at most 2 repair steps. Only reference elements you can actually SEE in the '
    'screenshot. If an unexpected dialog is covering the workflow, the repair is to dismiss '
    'it by clicking its own button. If you cannot see a way, return "repair_steps":[].'
)


def ask_claude(prompt, images, timeout=120):
    """Claude via the CLI, vision enabled. Internal inference — permitted by the spec."""
    args = ['claude', '-p', '--model', 'sonnet', '--system-prompt', SYSTEM,
            '--setting-sources', '', '--no-session-persistence',
            '--disallowedTools', 'Bash,Write,Edit,WebFetch,WebSearch,Task',
            '--allowedTools', 'Read', '--permission-mode', 'bypassPermissions']
    for d in {os.path.dirname(p) for p in images}:
        args += ['--add-dir', d]
    full = '\n'.join(f'Read this image first: {p}' for p in images) + '\n\n' + prompt
    out = subprocess.run(args, input=full, capture_output=True, text=True,
                         timeout=timeout, shell=True).stdout
    s, e = out.find('{'), out.rfind('}')
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(out[s:e + 1])
    except json.JSONDecodeError:
        return None


def find_by_description(desc, screen):
    """Ground a described element WITHOUT the accessibility tree: Claude names what it sees,
    we ask it for the normalized box on the same image. Vision only, no DOM."""
    p = os.path.join(ORG, 'state', 'repair_ground.png')
    cv2.imwrite(p, screen)
    r = ask_claude(
        f'Locate this element: "{desc}". Reply ONLY with '
        '{"found":true,"box":[x,y,w,h]} where the box is NORMALIZED 0-1 of the full image.',
        [p])
    if r and r.get('found') and len(r.get('box', [])) == 4:
        return [float(v) for v in r['box']]
    return None


def repair(skill_dir, blocked_state, why):
    with open(os.path.join(skill_dir, sorted(f for f in os.listdir(skill_dir)
                                             if f.startswith('v') and f.endswith('.json'))[-1]),
              encoding='utf-8') as f:
        skill = json.load(f)
    st = skill['states'].get(blocked_state)
    if not st:
        return {'ok': False, 'why': 'unknown state'}

    journal.emit('recovery_started', skill=skill['skill_id'], state=blocked_state, reason=why)
    live = matcher.grab()
    live_p = os.path.join(ORG, 'state', 'repair_live.png')
    cv2.imwrite(live_p, live)
    imgs = [live_p]
    crop = st['action'].get('target', {}).get('crop')
    if crop and os.path.exists(os.path.join(skill_dir, crop)):
        imgs.append(os.path.join(skill_dir, crop))

    plan = ask_claude(
        f'Skill "{skill["skill_id"]}" stalled at state {blocked_state}.\n'
        f'Failure: {why}\n'
        f'Intended action: {json.dumps(st["action"].get("type"))}\n'
        f'The second image is the element it expected. The first image is the live screen.\n'
        f'What single repair gets this workflow moving again?', imgs)
    if not plan:
        return {'ok': False, 'why': 'no plan from model'}
    journal.emit('repair_plan', skill=skill['skill_id'], state=blocked_state,
                 diagnosis=plan.get('diagnosis'), steps=len(plan.get('repair_steps') or []))
    steps = plan.get('repair_steps') or []
    if not steps:
        return {'ok': False, 'why': f'model saw no repair: {plan.get("diagnosis")}'}

    m = motor_mod.Motor()
    patch_states, fid = [], 0
    for s in steps[:2]:
        desc = s.get('description') or ''
        before = matcher.grab()
        box = find_by_description(desc, before)
        if not box:
            return {'ok': False, 'why': f'could not ground "{desc}"'}
        fid += 1
        act = {'action_id': str(uuid.uuid4())[:8], 'source_frame_id': fid,
               'timestamp': time.time_ns() // 1_000_000, 'intent': f'repair: {desc}',
               'target_region': box, 'confidence': 0.8,
               'expected_transition': 'screen answers', 'timeout_ms': 4000, 'risk': 'low'}
        r = m.click(act, latest_frame_id=fid)
        if not r['ok']:
            return {'ok': False, 'why': f'motor refused repair click: {r["why"]}'}
        time.sleep(0.5)
        after = matcher.grab()
        if not matcher.changed(before, after):
            return {'ok': False, 'why': f'repair click on "{desc}" changed nothing'}

        # Freeze what the repair clicked into crops — THIS is the permanent learning.
        h, w = before.shape[:2]
        cx, cy = int((box[0] + box[2] / 2) * w), int((box[1] + box[3] / 2) * h)
        names = {}
        for label, half in (('target', 64), ('context', 220)):
            c = before[max(0, cy - half):cy + half, max(0, cx - half):cx + half]
            n = f'r{int(time.time())}_{len(patch_states)}_{label}.png'
            cv2.imwrite(os.path.join(skill_dir, n), c)
            names[label] = n
        tight = before[max(0, cy - 16):cy + 16, max(0, cx - 60):cx + 60]
        names['tight'] = f'r{int(time.time())}_{len(patch_states)}_tight.png'
        cv2.imwrite(os.path.join(skill_dir, names['tight']), tight)
        patch_states.append((desc, names))

    # Patch: repair states are inserted BEFORE the blocked state, and are OPTIONAL —
    # skip_if_absent means a normal run (no surprise dialog) walks straight past them
    # without a single model call. That is what "the same exception never costs Claude
    # twice" means concretely.
    new = json.loads(json.dumps(skill))
    prev_key = None
    for i, (desc, names) in enumerate(patch_states):
        key = f'{blocked_state}_fix{i + 1}'
        new['states'][key] = {
            'required_visual_state': {'anchors': [names['context']], 'confidence_threshold': 0.87},
            'action': {'type': 'left_click', 'target': {'crop': names['target'],
                                                        'tight': names['tight'],
                                                        'context': names['context'],
                                                        'learned_from': desc}},
            'expected_transition': {'changed_near_target': True},
            'timeout_ms': 2500, 'retry_limit': 1, 'recovery_policy': 'repair',
            'skip_if_absent': True,
            'next_state': None,
        }
        if prev_key:
            new['states'][prev_key]['next_state'] = key
        prev_key = key
    new['states'][prev_key]['next_state'] = blocked_state
    for k, s in new['states'].items():
        if s.get('next_state') == blocked_state and not k.startswith(blocked_state):
            s['next_state'] = f'{blocked_state}_fix1'
    if new['entry_state'] == blocked_state:
        new['entry_state'] = f'{blocked_state}_fix1'
    new['version'] = skill['version'] + 1
    out = os.path.join(skill_dir, f'v{new["version"]}.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(new, f, indent=1)
    journal.emit('skill_patched', skill=skill['skill_id'], version=new['version'],
                 states_added=len(patch_states), diagnosis=plan.get('diagnosis'))
    return {'ok': True, 'version': new['version'], 'diagnosis': plan.get('diagnosis'),
            'states_added': len(patch_states), 'path': out}


if __name__ == '__main__':
    print(json.dumps(repair(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'manual'), indent=1))
