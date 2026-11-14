"""Trajectory → visual state machine.

Segmentation: clicks and coalesced typing arrive pre-merged from the recorder. Here we
cut anchor crops from frame_before at the demonstrated cursor position, chain states, and
attach expected transitions (frame_after exists = the screen visibly answered the action).
Coordinates are never stored as the target — crops are; the runtime clicks where the crop
MATCHES today, not where it sat during the demo.
"""
import json, os, sys
import cv2

TARGET = 64      # px half-size of target crop
CONTEXT = 220    # px half-size of context crop


def compile_skill(skill_dir, name):
    frames = os.path.join(skill_dir, 'frames')
    actions, meta = [], {}
    with open(os.path.join(skill_dir, 'trajectory.jsonl'), encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            if r.get('meta'):
                meta = r
            else:
                actions.append(r)
    actions.sort(key=lambda r: r['ts'])
    W, H = meta.get('screen', [1920, 1080])

    states, sid = {}, 0
    for a in actions:
        typ = a['input']['type']
        if typ.endswith('_click') and a.get('frame_before'):
            img = cv2.imread(os.path.join(frames, a['frame_before']))
            if img is None:
                continue
            cx = int(a['cursor_position_normalized'][0] * W)
            cy = int(a['cursor_position_normalized'][1] * H)
            crops = {}
            for label, half in (('target', TARGET), ('context', CONTEXT)):
                x0, y0 = max(0, cx - half), max(0, cy - half)
                crop = img[y0:cy + half, x0:cx + half]
                p = f's{sid + 1}_{label}.png'
                cv2.imwrite(os.path.join(skill_dir, p), crop)
                crops[label] = p
                if label == 'context':
                    # where the demo cursor sits INSIDE this crop — the crop clips at
                    # screen edges, so this is not always the center. The matcher's
                    # label-strip disambiguation is cut around this point.
                    crops['context_cursor'] = [cx - x0, cy - y0]
            # tight band: the element's own pixels, excluding neighbors above/below.
            # In a shuffled list the square crop carries the OLD neighbors and drags
            # confidence to 0.81 (measured); this 120x32 band is what actually moved.
            tx0, ty0 = max(0, cx - 60), max(0, cy - 16)
            tight = img[ty0:cy + 16, tx0:cx + 60]
            crops['tight'] = f's{sid + 1}_tight.png'
            cv2.imwrite(os.path.join(skill_dir, crops['tight']), tight)
            sid += 1
            states[f's{sid}'] = {
                'required_visual_state': {'anchors': [crops['context']], 'confidence_threshold': 0.87},
                'action': {'type': 'left_click', 'target': {'crop': crops['target'],
                                                            'tight': crops['tight'],
                                                            'context': crops['context'],
                                                            'context_cursor': crops['context_cursor'],
                                                            'demo_position': a['cursor_position_normalized']}},
                'expected_transition': {'changed_near_target': True,
                                        'after_ref': a.get('frame_after')},
                'timeout_ms': 6000, 'retry_limit': 1, 'recovery_policy': 'repair',
                'next_state': None,
            }
        elif typ == 'type':
            sid += 1
            states[f's{sid}'] = {
                'required_visual_state': {'anchors': [], 'confidence_threshold': 0},
                'action': {'type': 'type', 'text': a['input']['text']},
                'expected_transition': {'changed_near_target': True},
                'timeout_ms': 4000, 'retry_limit': 1, 'recovery_policy': 'repair',
                'next_state': None,
            }
        elif typ == 'key':
            sid += 1
            states[f's{sid}'] = {
                'required_visual_state': {'anchors': [], 'confidence_threshold': 0},
                'action': {'type': 'key', 'key': a['input']['key']},
                'expected_transition': {},
                'timeout_ms': 3000, 'retry_limit': 1, 'recovery_policy': 'repair',
                'next_state': None,
            }
    keys = list(states)
    for i, k in enumerate(keys[:-1]):
        states[k]['next_state'] = keys[i + 1]
    skill = {'skill_id': name, 'version': 1, 'entry_state': keys[0] if keys else None,
             'screen': [W, H], 'states': states}
    out = os.path.join(skill_dir, 'v1.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(skill, f, indent=1)
    print(f'compiled {len(states)} states -> {out}')
    return skill


if __name__ == '__main__':
    d = sys.argv[1]
    compile_skill(d, os.path.basename(d.rstrip('/\\')))
