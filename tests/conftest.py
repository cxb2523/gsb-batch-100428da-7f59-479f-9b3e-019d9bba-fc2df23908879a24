"""Shared fixtures for crawler resume tests.

The tests never touch a real site: network access is replaced by an in-memory
fake image grabber that emulates the Range-resume behavior of
``comiccrawler.grabber.grabimg``.
"""

import sys
from configparser import ConfigParser
from pathlib import Path
from types import ModuleType

import pytest
from worker import Event

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from comiccrawler import mods as mods_pkg
from comiccrawler.episode import Episode
from comiccrawler.grabber import ImgResult
from comiccrawler.mission import Mission
from comiccrawler.channel import Channel
from comiccrawler import crawler as crawler_mod


class FakeConfig:
	"""Minimal stand-in for a module config section."""

	def __init__(self):
		self._parser = ConfigParser()
		self._parser["DEFAULT"] = {
			"originalfilename": "false",
			"noepfolder": "false",
		}

	def getboolean(self, key, fallback=None):
		try:
			return self._parser["DEFAULT"].getboolean(key)
		except ValueError:
			return fallback

	def get(self, key, fallback=None):
		return self._parser["DEFAULT"].get(key, fallback)


def make_module():
	"""Build and register a fake downloader module."""
	mod = ModuleType("fake_mod")
	mod.name = "fake"
	mod.domain = ["faketest.local"]
	mod.config = FakeConfig()
	mod.rest = 0
	mods_pkg.mod_loader.mods.add(mod)
	mods_pkg.mod_loader.domain_index["faketest.local"] = mod
	return mod


@pytest.fixture(autouse=True)
def isolate_fake_module():
	"""Remove the fake module from the global loader after each test."""
	yield
	mods_pkg.mod_loader.mods = {
		m for m in mods_pkg.mod_loader.mods
		if getattr(m, "name", None) != "fake"
	}
	mods_pkg.mod_loader.domain_index.pop("faketest.local", None)


def make_mission(tmp_path, image_count):
	"""Create a one-image-per-page mission served entirely from memory."""
	mod = make_module()
	url = "http://faketest.local/comic/1"
	mission = Mission.__new__(Mission)
	mission.title = "fake-comic"
	mission.url = url
	mission.module = mod
	mission.state = "INIT"
	mission.last_update = None
	images = [
		f"http://faketest.local/img/{index}.jpg"
		for index in range(1, image_count + 1)
	]
	mission.episodes = [
		Episode(
			title="ep-1",
			url=url,
			current_url=url,
			image=images,
		)
	]
	return mission, images


class FakeResponse:
	def __init__(self, url, status_code=200, headers=None):
		self.url = url
		self.status_code = status_code
		self.headers = headers or {}
		self.history = []


class FakeGrabber:
	"""Drop-in replacement for ModuleGrabber.img used inside Crawler.

	It mirrors grabimg's resume contract:

	* an existing ``.part`` file triggers a ``Range: bytes=N-`` header;
	* a 200 response restarts the file, a 206 appends to it;
	* a failure leaves the bytes written so far in the ``.part`` file.
	"""

	def __init__(self, payloads, fail=None):
		# payloads: {image_url: full file bytes}
		self.payloads = payloads
		# fail: {image_url: callable(attempt, headers, tempfile) -> Exception}
		# the callable may write partial bytes into tempfile before raising.
		self.fail = fail or {}
		self.attempts = {}
		self.calls = []
		self.range_requests = []

	def img(self, url, *, tempfile=None, headers=None, on_opened=None,
			**kwargs):
		self.calls.append(url)
		attempt = self.attempts.get(url, 0) + 1
		self.attempts[url] = attempt
		headers = dict(headers or {})

		# mirror grabimg: derive the Range header from the .part fragment
		loaded = 0
		if tempfile:
			part = Path(tempfile)
			if part.exists():
				loaded = part.stat().st_size
		if loaded:
			headers["Range"] = f"bytes={loaded}-"
			self.range_requests.append((url, headers["Range"]))

		failure = self.fail.get(url)
		if failure is not None:
			err = failure(attempt, headers, tempfile)
			if err is not None:
				if on_opened:
					on_opened(FakeResponse(url, getattr(
						getattr(err, "response", None), "status_code", 200
					)))
				raise err

		payload = self.payloads[url]
		if tempfile:
			# 200 means the server ignores/reset the range; grabimg restarts.
			if loaded:
				status_code = 206
			else:
				status_code = 200
				loaded = 0
			response = FakeResponse(url, status_code)
			if on_opened:
				on_opened(response)
			part.parent.mkdir(parents=True, exist_ok=True)
			mode = "ab" if loaded else "wb"
			with part.open(mode) as f:
				f.write(payload[loaded:])
			return ImgResult(response, tempfile=tempfile)

		response = FakeResponse(url)
		if on_opened:
			on_opened(response)
		return ImgResult(response, b=payload)


class EventRecorder:
	"""Minimal Channel subscriber that records fired events."""

	def __init__(self):
		self.events = []

	def fire(self, event, *args, **kwargs):
		# Mimic worker.EventEmitter.fire: wrap raw args into an Event.
		if not isinstance(event, Event):
			event = Event(event, *args, **kwargs)
		self.events.append(event)


@pytest.fixture
def event_channel(monkeypatch):
	"""Replace download_ch with an isolated channel for each test."""
	channel = Channel()
	recorder = EventRecorder()
	channel.pool.add(recorder)
	monkeypatch.setattr(crawler_mod, "download_ch", channel)
	return channel, recorder


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
	"""Never enter the worker event loop during error backoff."""
	monkeypatch.setattr(crawler_mod, "sleep", lambda _timeout: None)


def event_data(recorder, name):
	return [event.data for event in recorder.events if event.name == name]


def payload_for(images, index, content=None):
	"""Return (url, bytes) for the 1-based page ``index``."""
	url = images[index - 1]
	if content is None:
		content = bytes([0xFF, 0xD8, 0xFF]) + bytes([index]) * 8
	return url, content
