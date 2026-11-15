# rpachallenge

* pip install -r requirements.txt
* rfbrowser init
* python task.py

Each engine run ends in one of four outcomes: `new_record`, `no_new_record`,
`unparseable_result`, or `page_error`. Only a new record updates
`record_time_<engine>` and writes a `record_<engine>_<date>_time<ms>ms.png`
screenshot; the failure outcomes leave both untouched and log the engine name
with the reason.

Run the local tests (no network or browser required):

* `python -m pytest tests`

![Current personal best which is recorded](record_rpabrowser_js_20122020_time47ms.png?raw=true "Current personal best which is recorded")
