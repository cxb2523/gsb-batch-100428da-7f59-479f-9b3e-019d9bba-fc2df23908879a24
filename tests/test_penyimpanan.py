"""Test penerimaan lokal untuk penyimpanan hasil scraping dan bersih-bersih cookie.

Semua test memakai data palsu dan tidak memerlukan browser/Playwright yang
benar-benar berjalan. Jalankan dari root repo:

    python -m pytest tests/ -q
"""

import csv
import json
import os
import sys

import pytest

from pyinstadump import konstanta
from pyinstadump import pengikis as modul_pengikis
from pyinstadump.pengikis import PengikisInstagram
from pyinstadump.utilitas import (
    hapus_cookie_sementara,
    path_cookie_sementara,
    tulis_cookie_sementara,
)


def baca_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def buat_pengikis(tmp_path, mode="following", username="rozhak.official"):
    return PengikisInstagram(
        target_username=username,
        mode_kikis=mode,
        file_cookie=str(tmp_path / "cookie.json"),
    )


class PlaywrightPalsu:
    """Pengganti sync_playwright() yang langsung keluar dari blok with."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_nama_file_parsial_memuat_mode_dan_username(tmp_path):
    p = buat_pengikis(tmp_path, mode="followers", username="target.akun")
    p.set_output_file(str(tmp_path / "data" / "followers_target.akun.csv"))

    nama = os.path.basename(p._nama_file_parsial())
    assert nama == "followers_target.akun_partial.csv"
    assert "followers" in nama
    assert "target.akun" in nama


@pytest.mark.parametrize("mode", ["followers", "following"])
def test_simpan_parsial_header_dan_isi(tmp_path, mode):
    p = buat_pengikis(tmp_path, mode=mode, username="target.akun")
    p.set_output_file(str(tmp_path / "data" / f"{mode}_target.akun.csv"))
    p.hasil_scrape = [("user_a", "Nama A"), ("user_b", "Nama B")]

    path = p.simpan_hasil_parsial()
    assert path is not None and os.path.exists(path)

    baris = baca_csv(path)
    assert baris[0] == konstanta.HEADER_CSV == ["Username", "Full Name"]
    assert baris[1:] == [["user_a", "Nama A"], ["user_b", "Nama B"]]


def test_interrupt_berulang_menimpa_file_yang_sama(tmp_path):
    p = buat_pengikis(tmp_path, mode="following", username="target.akun")
    p.set_output_file(str(tmp_path / "data" / "following_target.akun.csv"))

    p.hasil_scrape = [("user_a", "Nama A")]
    path_pertama = p.simpan_hasil_parsial()

    p.hasil_scrape = [("user_a", "Nama A"), ("user_b", "Nama B")]
    path_kedua = p.simpan_hasil_parsial()

    assert path_pertama == path_kedua
    daftar = sorted(os.listdir(os.path.dirname(path_kedua)))
    assert daftar == ["following_target.akun_partial.csv"]

    baris = baca_csv(path_kedua)
    assert len(baris) == 3
    assert baris[2] == ["user_b", "Nama B"]


def test_tanpa_data_tidak_menulis_file_parsial(tmp_path):
    p = buat_pengikis(tmp_path, mode="following", username="tanpa.data")
    p.set_output_file(str(tmp_path / "data" / "following_tanpa.data.csv"))

    path = p.simpan_hasil_parsial()
    assert path is None
    assert p.status_akhir == "tidak_ada_data"
    assert not os.path.exists(str(tmp_path / "data"))
    assert not os.path.isdir(tmp_path / "data")


def test_tanpa_data_tidak_menulis_csv_final(tmp_path):
    p = buat_pengikis(tmp_path, mode="followers", username="kosong")
    target = tmp_path / "followers_kosong.csv"

    assert p.simpan_ke_csv(str(target)) is False
    assert not target.exists()


def test_csv_final_header_dan_isi(tmp_path):
    p = buat_pengikis(tmp_path, mode="following", username="target.akun")
    p.hasil_scrape = [("user_a", "Nama A")]
    target = tmp_path / "data" / "following_target.akun.csv"

    assert p.simpan_ke_csv(str(target)) is True
    assert baca_csv(target) == [["Username", "Full Name"], ["user_a", "Nama A"]]


def test_file_parsial_tidak_tercampur_dengan_nama_final(tmp_path):
    p = buat_pengikis(tmp_path, mode="following", username="target.akun")
    p.set_output_file(str(tmp_path / "data" / "following_target.akun.csv"))
    p.hasil_scrape = [("user_a", "Nama A")]

    parsial = p.simpan_hasil_parsial()
    final = tmp_path / "data" / "following_target.akun.csv"
    assert p.simpan_ke_csv(str(final)) is True

    assert os.path.basename(parsial) == "following_target.akun_partial.csv"
    assert parsial != str(final)
    assert sorted(os.listdir(tmp_path / "data")) == [
        "following_target.akun.csv",
        "following_target.akun_partial.csv",
    ]


def test_cookie_sementara_berada_di_folder_cookies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = path_cookie_sementara()
    assert os.path.normpath(path).startswith(
        os.path.normpath(str(tmp_path / "cookies")) + os.sep
    )
    assert os.path.basename(path) == "temp_cookie_state.json"


def test_tulis_dan_hapus_cookie_sementara(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert hapus_cookie_sementara() is False

    path = tulis_cookie_sementara({"cookies": [], "origins": []})
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"cookies": [], "origins": []}
    assert not os.path.exists(tmp_path / "temp_cookie_state.json")

    assert hapus_cookie_sementara() is True
    assert not os.path.exists(path)
    # Direktori kerja tidak tercemar.
    assert os.listdir(tmp_path) == ["cookies"]


def test_tulis_cookie_sementara_atomik_tanpa_file_tmp_tertinggal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tulis_cookie_sementara({"cookies": [], "origins": []})
    assert sorted(os.listdir(tmp_path / "cookies")) == ["temp_cookie_state.json"]


def test_jalankan_error_menyimpan_parsial_lalu_raise(tmp_path, monkeypatch):
    """Error di tengah scraping harus menyimpan parsial dan meneruskan exception."""
    p = buat_pengikis(tmp_path, mode="following", username="target.akun")
    p.set_output_file(str(tmp_path / "data" / "following_target.akun.csv"))
    p.hasil_scrape = [("user_a", "Nama A")]

    def gagal():
        raise RuntimeError("popup tertutup")

    monkeypatch.setattr(p, "_buka_browser", gagal)
    monkeypatch.setattr(p, "tutup", lambda: None)
    monkeypatch.setattr(modul_pengikis, "sync_playwright", PlaywrightPalsu)

    with pytest.raises(RuntimeError):
        p.jalankan()

    parsial = tmp_path / "data" / "following_target.akun_partial.csv"
    assert parsial.exists()
    assert baca_csv(parsial) == [["Username", "Full Name"], ["user_a", "Nama A"]]


def test_jalankan_ctrl_c_menyimpan_parsial_lalu_raise(tmp_path, monkeypatch):
    """KeyboardInterrupt harus menyimpan parsial sebelum diteruskan."""
    p = buat_pengikis(tmp_path, mode="followers", username="target.akun")
    p.set_output_file(str(tmp_path / "data" / "followers_target.akun.csv"))
    p.hasil_scrape = [("user_a", "Nama A"), ("user_b", "Nama B")]

    def terganggu():
        raise KeyboardInterrupt

    monkeypatch.setattr(p, "_buka_browser", terganggu)
    monkeypatch.setattr(p, "tutup", lambda: None)
    monkeypatch.setattr(modul_pengikis, "sync_playwright", PlaywrightPalsu)

    with pytest.raises(KeyboardInterrupt):
        p.jalankan()

    parsial = tmp_path / "data" / "followers_target.akun_partial.csv"
    assert parsial.exists()
    assert baca_csv(parsial)[1:] == [["user_a", "Nama A"], ["user_b", "Nama B"]]


def test_pengikis_tidak_memasang_handler_sigint_global(monkeypatch):
    """Konstruksi scraper tidak boleh mengubah handler SIGINT global."""
    import signal

    sebelum = signal.getsignal(signal.SIGINT)
    PengikisInstagram("target", "following", "cookie.json")
    assert signal.getsignal(signal.SIGINT) == sebelum


# ---------------------------------------------------------------------------
# Uji end-to-end jalur keluar entry point main()
# ---------------------------------------------------------------------------

def jalankan_main(tmp_path, monkeypatch, data=None, langkah_gagal=None,
                  buka_browser=None):
    """Menjalankan main() dengan langkah scraping palsu dan cwd di tmp_path.

    Args:
        tmp_path: Direktori kerja sementara.
        monkeypatch: Fixture pytest.
        data: Entri palsu yang dianggap sudah terkumpul saat browser dibuka.
        langkah_gagal: Daftar pasangan (nama_method, exception) yang harus
            melempar exception saat dipanggil.
        buka_browser: Stub _buka_browser opsional; default mengisi data palsu.
    """
    from pyinstadump import main as modul_main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(modul_pengikis, "sync_playwright", PlaywrightPalsu)
    monkeypatch.setattr(os, "system", lambda *a, **k: 0)

    if buka_browser is None:
        def buka_browser(self):
            self.hasil_scrape = list(data or [])

    monkeypatch.setattr(PengikisInstagram, "_buka_browser", buka_browser)
    for nama in (
        "_login_dengan_cookie",
        "_navigasi_ke_target",
        "_buka_popup_daftar",
        "_gulir_dan_muat_data",
        "_ekstrak_data_pengguna",
    ):
        monkeypatch.setattr(PengikisInstagram, nama, lambda self: None)
    monkeypatch.setattr(PengikisInstagram, "tutup", lambda self: None)

    for nama, exception in langkah_gagal or []:
        def gagal(self, _exc=exception):
            raise _exc

        monkeypatch.setattr(PengikisInstagram, nama, gagal)

    monkeypatch.setattr(
        sys, "argv",
        ["prog", "--username", "target.akun", "--mode", "following",
         "--cookie", "sessionid=abc; csrftoken=def"],
    )
    return modul_main.main()


def test_main_selesai_normal_menulis_final_dan_menghapus_cookie(tmp_path, monkeypatch):
    kode = jalankan_main(tmp_path, monkeypatch, data=[("user_a", "Nama A")])

    assert kode == 0
    final = tmp_path / "data" / "following_target.akun.csv"
    assert baca_csv(final) == [["Username", "Full Name"], ["user_a", "Nama A"]]
    assert not (tmp_path / "cookies" / "temp_cookie_state.json").exists()
    assert not (tmp_path / "temp_cookie_state.json").exists()
    # Hanya folder data/ dan cookies/ (folder cookie yang memang ada di repo).
    assert sorted(os.listdir(tmp_path)) == ["cookies", "data"]
    assert os.listdir(tmp_path / "cookies") == []


def test_main_exception_menyimpan_parsial_dan_menghapus_cookie(tmp_path, monkeypatch):
    kode = jalankan_main(
        tmp_path, monkeypatch,
        data=[("user_a", "Nama A"), ("user_b", "Nama B")],
        langkah_gagal=[("_gulir_dan_muat_data", RuntimeError("koneksi putus"))],
    )

    assert kode == 1
    parsial = tmp_path / "data" / "following_target.akun_partial.csv"
    assert baca_csv(parsial) == [
        ["Username", "Full Name"],
        ["user_a", "Nama A"],
        ["user_b", "Nama B"],
    ]
    assert not (tmp_path / "data" / "following_target.akun.csv").exists()
    assert not (tmp_path / "cookies" / "temp_cookie_state.json").exists()
    assert not (tmp_path / "temp_cookie_state.json").exists()


def test_main_ctrl_c_menyimpan_parsial_dan_menghapus_cookie(tmp_path, monkeypatch):
    kode = jalankan_main(
        tmp_path, monkeypatch,
        data=[("user_a", "Nama A")],
        langkah_gagal=[("_gulir_dan_muat_data", KeyboardInterrupt())],
    )

    assert kode == 130
    parsial = tmp_path / "data" / "following_target.akun_partial.csv"
    assert baca_csv(parsial) == [["Username", "Full Name"], ["user_a", "Nama A"]]
    assert not (tmp_path / "data" / "following_target.akun.csv").exists()
    assert not (tmp_path / "cookies" / "temp_cookie_state.json").exists()
    assert not (tmp_path / "temp_cookie_state.json").exists()


def test_main_tanpa_data_tidak_menulis_csv_apa_pun(tmp_path, monkeypatch):
    kode = jalankan_main(tmp_path, monkeypatch, data=[])

    assert kode == 1

    assert not (tmp_path / "data" / "following_target.akun.csv").exists()
    assert not (tmp_path / "data" / "following_target.akun_partial.csv").exists()
    assert not (tmp_path / "cookies" / "temp_cookie_state.json").exists()
    assert not (tmp_path / "temp_cookie_state.json").exists()
    # Folder data boleh ada, tetapi tidak berisi file CSV apa pun.
    assert os.listdir(tmp_path / "data") == []


def test_main_dua_kali_interrupt_hanya_satu_file_parsial(tmp_path, monkeypatch):
    """Interrupt berulang pada target yang sama menimpa file, tidak menumpuk."""
    jalankan_main(
        tmp_path, monkeypatch,
        data=[("user_a", "Nama A")],
        langkah_gagal=[("_gulir_dan_muat_data", KeyboardInterrupt())],
    )
    jalankan_main(
        tmp_path, monkeypatch,
        data=[("user_a", "Nama A"), ("user_b", "Nama B")],
        langkah_gagal=[("_gulir_dan_muat_data", KeyboardInterrupt())],
    )

    berkas = sorted(os.listdir(tmp_path / "data"))
    assert berkas == ["following_target.akun_partial.csv"]

    baris = baca_csv(tmp_path / "data" / "following_target.akun_partial.csv")
    assert baris == [
        ["Username", "Full Name"],
        ["user_a", "Nama A"],
        ["user_b", "Nama B"],
    ]


def test_main_cookie_sementara_dibuat_di_folder_cookies(tmp_path, monkeypatch):
    """Saat scraping berjalan, berkas cookie sementara berada di cookies/."""
    dari_cookie = {}

    def buka_browser(self):
        dari_cookie["path"] = self.file_cookie
        dari_cookie["ada"] = os.path.exists(self.file_cookie)
        self.hasil_scrape = [("user_a", "Nama A")]

    jalankan_main(tmp_path, monkeypatch, buka_browser=buka_browser)

    path = os.path.normpath(dari_cookie["path"])
    assert path.startswith(os.path.normpath(str(tmp_path / "cookies")) + os.sep)
    assert dari_cookie["ada"] is True
    assert not os.path.exists(path)

