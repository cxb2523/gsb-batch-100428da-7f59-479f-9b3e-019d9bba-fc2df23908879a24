"""Benchmark mock application (spec §10): menu → item → 3 fields → NEXT → rows screen →
row click → modal → CONFIRM → randomized 1-3s spinner → DONE. Window position, row order,
loading time and accent color randomized per launch. Ground truth to hits.jsonl.
Surprise modal injected by writing {'cmd':'surprise'} to cmd.json (the repair test).
"""
import json, os, random, time
import tkinter as tk

DIR = os.path.dirname(os.path.abspath(__file__))
HITS = os.path.join(DIR, 'mock_hits.jsonl')
CMD = os.path.join(DIR, 'mock_cmd.json')


LAYOUT = os.path.join(DIR, 'mock_layout.json')


def log(rec):
    rec['ts'] = time.time_ns() // 1_000_000
    with open(HITS, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec) + '\n')


class Mock:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('MOCK CRM')
        # Topmost: this desktop has a night shift (ORA brain, apply lane) that maximizes
        # real Chrome over everything. A coverable benchmark window loses 99/100 runs
        # to z-order (measured twice, 2026-08-01). Uncoverable = benchmarkable.
        self.root.attributes('-topmost', True)
        rnd = random.Random()
        x, y = rnd.randrange(40, 400), rnd.randrange(40, 200)
        self.root.geometry(f'900x640+{x}+{y}')
        self.accent = rnd.choice(['#2563eb', '#1d4ed8', '#3b82f6'])
        self.rnd = rnd
        self.frame = None
        self.screen_menu()
        self.root.after(300, self.poll_cmd)

    def clear(self):
        if self.frame:
            self.frame.destroy()
        self.frame = tk.Frame(self.root, bg='#14141c')
        self.frame.pack(fill='both', expand=True)
        self.named = {}

    def name_widget(self, key, w):
        self.named[key] = w

    def dump_layout(self):
        """Widget positions for the DEMO DRIVER only — it plays the human's eyes during the
        demonstration. Reflex itself never reads this file; replay is pixels-only."""
        out = {}
        for k, w in list(self.named.items()):
            try:
                out[k] = [w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()]
            except Exception:
                pass
        with open(LAYOUT, 'w', encoding='utf-8') as f:
            json.dump({'ts': time.time_ns() // 1_000_000, 'widgets': out}, f)

    def dump_soon(self):
        self.root.after(200, self.dump_layout)

    # screen 1: menu
    def screen_menu(self):
        self.clear()
        bar = tk.Frame(self.frame, bg='#0c0c12')
        bar.pack(fill='x')
        b = tk.Button(bar, text='Reports', bg=self.accent, fg='white', font=('Consolas', 12),
                  command=self.open_menu)
        b.pack(side='left', padx=8, pady=6)
        self.name_widget('btn:Reports', b)
        self.body = tk.Frame(self.frame, bg='#14141c')
        self.body.pack(fill='both', expand=True)
        tk.Label(self.body, text='MOCK CRM — HOME', fg='#8888aa', bg='#14141c',
                 font=('Consolas', 18)).place(relx=0.5, rely=0.4, anchor='center')
        self.dump_soon()

    def open_menu(self):
        log({'step': 'menu_opened'})
        m = tk.Frame(self.body, bg='#1e1e2a', highlightbackground='white', highlightthickness=1)
        m.place(x=10, y=2)
        b = tk.Button(m, text='New Monthly Report', bg='#1e1e2a', fg='white', font=('Consolas', 11),
                  command=self.screen_form)
        b.pack(padx=6, pady=4)
        self.name_widget('menu:New Monthly Report', b)
        self.dump_soon()

    # screen 2: form
    def screen_form(self):
        log({'step': 'form_opened'})
        self.clear()
        tk.Label(self.frame, text='NEW REPORT', fg='white', bg='#14141c',
                 font=('Consolas', 16, 'bold')).pack(pady=14)
        self.fields = {}
        for name in ('Title', 'Owner', 'Month'):
            row = tk.Frame(self.frame, bg='#14141c')
            row.pack(pady=6)
            tk.Label(row, text=name, fg='#aaaacc', bg='#14141c', width=8,
                     font=('Consolas', 12)).pack(side='left')
            e = tk.Entry(row, font=('Consolas', 12), bg='#1e1e28', fg='white',
                         insertbackground='white', width=30)
            e.pack(side='left')
            e.bind('<KeyRelease>', lambda ev, n=name: log({'step': 'field', 'name': n,
                                                           'value': self.fields[n].get()}))
            self.fields[name] = e
            self.name_widget(f'field:{name}', e)
        b = tk.Button(self.frame, text='NEXT', bg=self.accent, fg='white', font=('Consolas', 13, 'bold'),
                  command=self.screen_rows)
        b.pack(pady=18)
        self.name_widget('btn:NEXT', b)
        self.dump_soon()

    # screen 3: rows
    def screen_rows(self):
        log({'step': 'rows_opened', 'fields': {k: v.get() for k, v in self.fields.items()}})
        self.clear()
        tk.Label(self.frame, text='SELECT ACCOUNT', fg='white', bg='#14141c',
                 font=('Consolas', 16, 'bold')).pack(pady=12)
        names = ['Acme Corp', 'Globex', 'Initech', 'Umbrella', 'Stark Industries']
        self.rnd.shuffle(names)
        self.target_row = 'Initech'          # semantic target survives the shuffle
        for n in names:
            b = tk.Button(self.frame, text=n, bg='#1e1e2a', fg='white', font=('Consolas', 12),
                          width=40, command=lambda n=n: self.open_modal(n))
            b.pack(pady=3)
            self.name_widget(f'row:{n}', b)
        self.dump_soon()

    def open_modal(self, name):
        log({'step': 'row_clicked', 'name': name, 'correct': name == self.target_row})
        self.modal = tk.Frame(self.frame, bg='#2a2a3a', highlightbackground='white',
                              highlightthickness=2)
        self.modal.place(relx=0.5, rely=0.5, anchor='center', width=360, height=170)
        tk.Label(self.modal, text=f'Generate report for\n{name}?', fg='white', bg='#2a2a3a',
                 font=('Consolas', 13)).pack(pady=16)
        b = tk.Button(self.modal, text='CONFIRM', bg=self.accent, fg='white',
                  font=('Consolas', 12, 'bold'), command=lambda: self.spinner(name))
        b.pack()
        self.name_widget('btn:CONFIRM', b)
        self.dump_soon()

    def spinner(self, name):
        log({'step': 'confirmed', 'name': name})
        self.clear()
        lbl = tk.Label(self.frame, text='GENERATING...', fg='#f59e0b', bg='#14141c',
                       font=('Consolas', 20, 'bold'))
        lbl.place(relx=0.5, rely=0.5, anchor='center')
        delay = self.rnd.randrange(1000, 3000)
        self.root.after(delay, lambda: self.done(name, delay))

    def done(self, name, delay):
        self.clear()
        tk.Label(self.frame, text='REPORT DONE ✔', fg='#10b981', bg='#14141c',
                 font=('Consolas', 24, 'bold')).place(relx=0.5, rely=0.5, anchor='center')
        log({'step': 'done', 'name': name, 'delay_ms': delay})

    # surprise modal — the repair benchmark
    def poll_cmd(self):
        try:
            if os.path.exists(CMD):
                with open(CMD, encoding='utf-8') as f:
                    c = json.load(f).get('cmd')
                os.remove(CMD)
                if c == 'surprise':
                    s = tk.Frame(self.root, bg='#4a1a1a', highlightbackground='white',
                                 highlightthickness=2)
                    s.place(relx=0.5, rely=0.35, anchor='center', width=380, height=150)
                    tk.Label(s, text='UNEXPECTED: session expiring', fg='white', bg='#4a1a1a',
                             font=('Consolas', 12, 'bold')).pack(pady=14)
                    tk.Button(s, text='DISMISS', bg='#991b1b', fg='white',
                              command=lambda: (log({'step': 'surprise_dismissed'}), s.destroy())).pack()
                    log({'step': 'surprise_shown'})
        except Exception:
            pass
        self.root.after(300, self.poll_cmd)


if __name__ == '__main__':
    Mock().root.mainloop()
