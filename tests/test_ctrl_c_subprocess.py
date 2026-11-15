"""Verifikasi jalur Ctrl+C nyata (KeyboardInterrupt) lewat proses terpisah.

Menjalankan main() di proses terpisah dengan langkah scraping palsu yang
memanipulasi hasil lalu menunggu, kemudian menghentikannya seperti Ctrl+C:

* POSIX: mengirim SIGINT sungguhan ke proses.
* Windows: subprocess tidak mendukung pengiriman SIGINT, sehingga proses
  anak memakai _thread.interrupt_main() yang membangkitkan KeyboardInterrupt
  di thread utama — efeknya sama dengan Ctrl+C pada jalur kode yang diuji.

Tidak memakai browser sungguhan.
"""

import os
import signal
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKRIP = r'''
import os, sys, threading, time
sys.path.insert(0, {repo!r})

import pyinstadump.pengikis as pk
from pyinstadump.pengikis import PengikisInstagram


class PlaywrightPalsu:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


pk.sync_playwright = PlaywrightPalsu
PengikisInstagram._buka_browser = lambda self: setattr(
    self, "hasil_scrape", [("user_a", "Nama A"), ("user_b", "Nama B")]
)
for nama in ("_login_dengan_cookie", "_navigasi_ke_target", "_buka_popup_daftar"):
    setattr(PengikisInstagram, nama, lambda self: None)
PengikisInstagram._ekstrak_data_pengguna = lambda self: None
PengikisInstagram.tutup = lambda self: None

PEMICU = {pemicu!r}


def pengawas():
    """Menunggu berkas pemicu lalu membangkitkan KeyboardInterrupt di main thread."""
    while not os.path.exists(PEMICU):
        time.sleep(0.02)
    import _thread
    _thread.interrupt_main()


threading.Thread(target=pengawas, daemon=True).start()


def menggantung(self):
    print("SIAP", flush=True)
    while True:
        time.sleep(0.02)


PengikisInstagram._gulir_dan_muat_data = menggantung

sys.argv = ["prog", "--username", "target.akun", "--mode", "following",
            "--cookie", "sessionid=abc; csrftoken=def"]
from pyinstadump.main import main
main()
'''


def jalankan_dan_interupsi(tmp_path):
    pemicu = tmp_path / "pemicu_interupsi"
    skrip = tmp_path / "jalankan.py"
    skrip.write_text(SKRIP.format(repo=REPO, pemicu=str(pemicu)), encoding="utf-8")

    proses = subprocess.Popen(
        [sys.executable, "-I", str(skrip)],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    keluaran = ""
    try:
        batas = time.time() + 60
        while time.time() < batas:
            baris = proses.stdout.readline()
            if not baris:
                break
            keluaran += baris
            if "SIAP" in keluaran:
                break

        assert "SIAP" in keluaran, f"skrip tidak siap: {keluaran[-2000:]}"
        assert (tmp_path / "cookies" / "temp_cookie_state.json").exists()

        pemicu.write_text("x", encoding="utf-8")
        sisa, _ = proses.communicate(timeout=60)
        keluaran += sisa or ""
    finally:
        if proses.poll() is None:
            proses.kill()
            proses.wait(timeout=30)

    return keluaran


def periksa_hasil(tmp_path, keluaran):
    assert not (tmp_path / "cookies" / "temp_cookie_state.json").exists(), keluaran[-2000:]
    assert not (tmp_path / "temp_cookie_state.json").exists(), keluaran[-2000:]

    parsial = tmp_path / "data" / "following_target.akun_partial.csv"
    assert parsial.exists(), keluaran[-2000:]
    isi = parsial.read_text(encoding="utf-8").splitlines()
    assert isi[0] == "Username,Full Name"
    assert isi[1] == "user_a,Nama A"
    assert isi[2] == "user_b,Nama B"

    # Tidak ada file final dan tidak ada file header-saja yang tertinggal.
    assert not (tmp_path / "data" / "following_target.akun.csv").exists()


def test_ctrl_c_membangkitkan_keyboard_interrupt_di_proses_terpisah(tmp_path):
    periksa_hasil(tmp_path, jalankan_dan_interupsi(tmp_path))


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows tidak mendukung pengiriman SIGINT ke subprocess")
def test_ctrl_c_sigint_sungguhan(tmp_path):
    skrip = tmp_path / "jalankan.py"
    # Tanpa pemicu: pengawas tidak pernah aktif.
    skrip.write_text(
        SKRIP.format(repo=REPO, pemicu=str(tmp_path / "tidak_ada")),
        encoding="utf-8",
    )
    proses = subprocess.Popen(
        [sys.executable, "-I", str(skrip)],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    keluaran = ""
    try:
        batas = time.time() + 60
        while time.time() < batas:
            baris = proses.stdout.readline()
            if not baris:
                break
            keluaran += baris
            if "SIAP" in keluaran:
                break
        assert "SIAP" in keluaran, keluaran[-2000:]

        proses.send_signal(signal.SIGINT)
        sisa, _ = proses.communicate(timeout=60)
        keluaran += sisa or ""
    finally:
        if proses.poll() is None:
            proses.kill()
            proses.wait(timeout=30)

    periksa_hasil(tmp_path, keluaran)
