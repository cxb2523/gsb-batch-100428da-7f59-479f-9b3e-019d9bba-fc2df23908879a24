import json

import pytest

import client_cli.params
import coms.coms


def _read(tmp_path, monkeypatch, data):
    path = tmp_path / 'params.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(client_cli.params.coms.coms, 'get_filename_for_read',
                        lambda name: str(path))
    monkeypatch.setattr(client_cli.params.coms.coms, 'get_filename_for_store',
                        lambda name: str(tmp_path / name))

    return client_cli.params.read_params_from_file(str(path))


def test_poll_settings_default_to_none(tmp_path, monkeypatch):
    params = _read(tmp_path, monkeypatch, {'search_string': 'самолет'})

    assert params['poll_timeout'] is None
    assert params['poll_interval'] is None


def test_poll_settings_are_read_from_params(tmp_path, monkeypatch):
    params = _read(tmp_path, monkeypatch, {
        'search_string': 'самолет',
        'poll_timeout_sec': 120,
        'poll_interval_sec': 5,
    })

    assert params['poll_timeout'] == 120
    assert params['poll_interval'] == 5


def test_invalid_poll_settings_fall_back_to_defaults(tmp_path, monkeypatch):
    params = _read(tmp_path, monkeypatch, {
        'search_string': 'самолет',
        'poll_timeout': 'abc',
        'poll_interval': 0,
    })

    assert params['poll_timeout'] is None
    assert params['poll_interval'] is None
