# Command line

```text
gmaps-collect AREA QUERY [options]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-n`, `--max-results N` | all | stop after N places |
| `--no-hours` | off | skip the opening-hours request per place |
| `-o`, `--output FILE` | stdout | write to a file |
| `-f`, `--format json\|jsonl\|csv` | from `-o`, else `json` | output format |
| `--proxy URL` | none | proxy URL (prefer `GMAPS_PROXY_URL`) |
| `-w`, `--workers N` | 4 | parallel requests, 1-16 |
| `--min-interval S` | 0.5 | seconds between requests |
| `-b`, `--buffer KM` | 5 | margin kept around the area box |
| `--cell-size M` | by area size | grid cell size in metres |
| `-q`, `--quiet` | off | no progress or statistics on stderr |
| `--version` | | print the version |

Exit codes: `0` success (also when nothing was found), `1` an error (unknown area, every request failed, bad
proxy settings), `130` interrupted.

`python -m gmaps_extractor` is the same command.
