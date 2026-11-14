"""Pemulihan: halaman mati, tab salah, renderer suspend."""

import ctypes
import io
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from . import state
from . import browser
from . import levels
from . import session
from .config import (LIST_URL)
from .jstemplates import (PLAYABLE_CHECK_JS)




def _url_asli_sekarang(pg=None):
    """URL ASLI halaman lewat location.href (round-trip ke renderer).
    page.url Playwright bisa BASI - server sudah me-redirect ke /signin
    atau halaman utama, tapi properti itu masih melapor '.play' lama.
    Keputusan 'masih di level?' tidak boleh memakai page.url. Return ''
    kalau renderer tidak bisa dibaca (= tidak terbukti keluar level)."""
    pg = pg if pg is not None else state.PAGE
    if pg is None:
        return ""
    # Gerbang aman: evaluate di renderer yang di-suspend Windows bisa
    # MENGHANG tanpa timeout (bug live yang pernah mematikan loop).
    # wait_for_load_state menerima timeout -> kalau dokumen belum siap,
    # jangan sentuh renderer sama sekali (URL dianggap belum terbaca;
    # pemanggil menunda keputusan, bukan menuduh level rusak).
    try:
        pg.wait_for_load_state("domcontentloaded", timeout=2500)
    except Exception:
        return ""
    # dua percobaan: evaluate bisa gagal sesaat kalau halaman baru saja
    # pindah (execution context lama sudah dibuang). Jeda kecil = redirect
    # sempat mendarat dan URL barunya terbaca.
    for _ in range(2):
        try:
            u = pg.evaluate("() => location.href")
            if isinstance(u, str) and u.strip():
                return u.strip()
        except Exception:
            pass
        time.sleep(0.4)
    return ""


def _nama_halaman(url):
    """Potongan URL untuk log (path terakhir, mis. 'signin' / '3.play')."""
    if not url:
        return "?"
    try:
        p = (urlparse(url).path or "").rstrip("/")
        return p.rsplit("/", 1)[-1] or "/"
    except Exception:
        return (url or "")[:60]


def _url_terbaca(url):
    """URL hasil evaluate bisa berbentuk aneh (string kosong, 'null',
    'about:blank', data:) - itu belum bisa dipakai sebagai bukti apa pun."""
    if not url:
        return False
    u = url.strip().lower()
    return u.startswith(("http://", "https://"))


def _bukan_halaman_level(url):
    """URL yang TERBACA dan JELAS bukan halaman lesson (logout, /signin,
    dashboard, halaman utama, OAuth). about:blank / halaman error / URL
    kosong TIDAK dihitung: di situ belum terbukti bot keluar dari level
    - keputusan lompat ditunda (bukan dianggap rusak)."""
    if not _url_terbaca(url):
        return False
    return not browser._di_lesson(url)


def _di_luar_level(url_asli):
    """Sudah pasti keluar dari halaman level? HANYA location.href yang
    dipakai sebagai bukti - page.url boleh basi (masih bilang '.play'
    padahal server sudah me-redirect ke /signin), jadi tidak dijadikan
    dasar keputusan apa pun. Return URL non-level, atau '' bila belum
    terbukti keluar level."""
    if _bukan_halaman_level(url_asli):
        return url_asli
    return ""


def _level_sesi_sekarang():
    """URL level yang SEDANG dikerjakan sesi ini. Utamakan state.last_url
    (level yang barusan terlihat), lalu jangkar level sesi - karena saat
    redirect logout state.last_url sudah tertimpa URL login/home.
    Syarat '.play' SAMA dengan aturan lama (target hanya bila URL
    berbentuk lesson) supaya reload normal tidak berubah."""
    for u in (state.last_url, getattr(state, "_lesson_anchor", "")):
        if u and ".play" in u:
            return u
    return None


def _serahkan_ke_patroli_login(target, n, real, ulang=False):
    """Halaman sudah keluar dari level kursus - ini BUKAN level rusak.
    Jangan dihitung, jangan lompati level: simpan level sesi ini lalu
    serahkan kendali ke patroli login; setelah login pulih patroli
    mengembalikan bot ke level tersebut."""
    state._recovery_counts.pop(target, None)
    if not ulang:
        state._recovery_pending_lesson = target
        state._lesson_anchor = target
        state._recovery_resume_fail = 0
        n_txt = f"{n}x pulih" if n else "pemulihan"
        posisi = (f"sekarang: {_nama_halaman(real)}" if _url_terbaca(real)
                  else "halaman sedang login/redirect")
        print(f"[RECOVERY] {n_txt}, halaman sudah keluar dari level kursus "
              f"({posisi}) - bukan level rusak, "
              f"TIDAK dilompati; serahkan ke patroli login (level sesi "
              f"{target.split('/')[-1]} ditahan untuk dilanjutkan)")
    else:
        posisi = (_nama_halaman(real) if _url_terbaca(real)
                  else "login/redirect")
        print(f"[RECOVERY] masih di luar level ({posisi}) - "
              f"tetap tunggu patroli login; level sesi "
              f"{target.split('/')[-1]} tidak dilompati")
    try:
        if _url_terbaca(real) and not browser._oauth_url(real):
            # Halaman OAuth (pilih akun Google / callback) = user SEDANG
            # login: jangan sentuh (patroli bisa me-redirect halaman).
            # URL tak terbaca juga tidak dikirim ke patroli - loop utama
            # tetap memanggil patroli dengan URL-nya sendiri. Selain itu:
            # jangan tunggu jeda patroli pada keputusan pertama - status
            # login dibutuhkan sekarang juga (pemanggilan ulang mengikuti
            # jeda patroli sendiri, tidak membanjiri).
            if not ulang:
                state._login_ck["terakhir"] = 0.0
            session._login_patrol(real)
    except Exception:
        pass
    state.last_action_time = time.time()
    return True


def _putuskan_ambang_recovery(target, n):
    """Ambang recovery berulang tercapai. SEBELUM menuduh level rusak,
    periksa URL ASLI browser: kalau halaman sudah keluar dari level
    (logout / redirect /signin / halaman utama) itu BUKAN bukti level
    rusak - jangan dihitung, jangan lompat. Kendali diserahkan ke patroli
    login; setelah login pulih ia mengembalikan bot ke level sesi ini.
    Hanya kalau URL asli MASIH halaman level, aturan lama berlaku:
    lompat ke pelajaran berikutnya sesuai urutan daftar.

    URL asli yang TAK TERBACA (renderer sibuk/navigasi) bukan bukti
    'masih di level' - melompat itu tindakan merusak (progres user
    maju), jadi keputusan ditunda sampai URL terbaca. Renderer yang
    benar-benar mati ditangani gerbang kesehatan di loop utama."""
    asli = _url_asli_sekarang()
    if state.NEEDS_LOGIN:
        # Sesi sedang mati: apa pun yang terlihat di halaman bukan bukti
        # level rusak (halaman bisa berupa sisa render lama / overlay
        # login). Jangan lompati level; tunggu sesi hidup lagi.
        return _serahkan_ke_patroli_login(target, n, asli)
    real = _di_luar_level(asli)
    if real:
        return _serahkan_ke_patroli_login(target, n, real)
    if not _url_terbaca(asli):
        print(f"[RECOVERY] {n}x pulih gagal; URL asli belum terbaca "
              f"(halaman sedang sibuk/navigasi) - TIDAK melompati level "
              f"(belum terbukti masih di level); coba reload sekali lagi")
        # Jangan diam menunggu selamanya: reload netral (tidak memajukan
        # progres) masih boleh dicoba - kalau halaman memang macet, ini
        # justru yang menyembuhkannya.
        try:
            state.PAGE.reload(timeout=25000)
            state.last_url = state.PAGE.url
            state.last_action_time = time.time()
            return True
        except Exception as e:
            print(f"[RECOVERY] reload juga gagal ({str(e)[:60]}) - "
                  f"pemulihan berikutnya akan mencoba lagi")
            return False
    # halaman memang MASIH level (URL asli, bukan page.url basi) ->
    # aturan lama: level ini rusak, lanjut ke urutan daftar.
    state.last_url = target
    state._lesson_anchor = target
    print(f"[RECOVERY] {n}x pulih gagal dan URL asli masih halaman "
          f"level ({_nama_halaman(asli)}) - dianggap level rusak, "
          f"lompat ke pelajaran berikutnya")
    if levels._skip_to_next_lesson("recovery berulang, level rusak"):
        # lompatan berhasil: jangkar sesi harus ikut level BARU - kalau
        # tidak, recovery berikutnya akan mengenali level lama sebagai
        # 'level sesi ini' dan memulihkan level yang sudah ditinggalkan.
        try:
            baru = browser._lesson_play_url(state.last_url or "")
        except Exception:
            baru = ""
        if baru and ".play" in baru:
            state._lesson_anchor = baru
        return True
    print("[RECOVERY] gagal lompat ke pelajaran berikutnya - "
          "pemulihan berikutnya akan mencoba lagi")
    return False


def _switch_to_playable_tab():
    """Kalau ada tab edclub lain yang jelas punya pekerjaan (token _clr /
    boxed aktif) sedangkan tab sekarang sunyi - pindah ke sana.
    Return True kalau pindah."""
    try:
        pages = [pg for pg in state.browser.contexts[0].pages
                 if pg is not state.PAGE]
    except Exception:
        return False
    best, best_score = None, 0
    for pg in pages:
        try:
            url = browser._real_url(pg)
            if not browser._is_edclub_url(url):
                continue
            if ".play" not in url:
                continue
            info = pg.evaluate("() => {" + PLAYABLE_CHECK_JS + "}")
            s = 0
            if info:
                if info.get("clr"):
                    s += 5
                elif info.get("boxed"):
                    s += 3
                if info.get("done"):
                    s -= 8
        except Exception:
            continue
        if s > best_score:
            best, best_score = pg, s
    if best is not None:
        print(f"[TAB] pindah ke tab lain yang ada kerjaannya: "
              f"{best.url.split('/')[-1]}")
        state.PAGE = best
        state.last_url = ""
        # jangkar ikut tab yang dipilih - recovery berikutnya harus
        # memulihkan level DI TAB INI, bukan level tab lama.
        try:
            u_best = browser._lesson_play_url(browser._real_url(best))
        except Exception:
            u_best = ""
        if u_best and ".play" in u_best:
            state._lesson_anchor = u_best
        state._recovery_pending_lesson = ""
        state.last_action_time = time.time()
        return True
    return False


def recover_and_restart_lesson():
    """Pulihkan halaman macet/kosong. UTAMA: RELOAD TAB YANG SAMA -
    tab baru hanya kalau reload gagal (tab benar2 mati) atau URL lesson
    tidak diketahui. (Dulu selalu tab baru: mengganggu user dan pernah
    bertabrakan dengan refresh manual user.)

    Ambang 3x TIDAK otomatis berarti level rusak: dulu logout/redirect
    ke login di tengah reload ikut terhitung lalu bot melompati level.
    Sekarang sebelum memutuskan, URL ASLI (location.href) diperiksa -
    lihat _putuskan_ambang_recovery."""
    state._last_recovery = time.time()

    # Handoff yang masih menggantung (halaman sudah keluar level, patroli
    # login yang pegang kendali): jangan reload halaman login/home -
    # cukup serahkan (idempoten) dan tunggu patroli mengembalikan ke level.
    pending = getattr(state, "_recovery_pending_lesson", "")
    if pending and browser._di_lesson(pending):
        real_pending = _url_asli_sekarang()
        if _url_terbaca(real_pending) and browser._di_lesson(real_pending):
            # sudah kembali ke level (patroli/user berhasil) - handoff
            # tuntas; hitungan lama tidak diwariskan ke level yang sama
            if not state.NEEDS_LOGIN:
                state._recovery_pending_lesson = ""
                state._recovery_counts.pop(pending, None)
        else:
            # satu putaran lagi menyerahkan kendali ke patroli
            # (idempoten); TIDAK me-reload halaman login/home dan
            # tidak melompati level. Handoff bertahan sampai sesi pulih
            # - menyerah berarti membiarkan bot mengerjakan halaman
            # tempat ia mendarat (= progres maju tanpa izin user).
            return _serahkan_ke_patroli_login(pending, 0, real_pending,
                                              ulang=True)

    target = _level_sesi_sekarang()

    if target:
        n = state._recovery_counts.get(target, 0) + 1
        state._recovery_counts[target] = n
        if n >= 3:
            # Ambang tercapai: putuskan level rusak ATAU halaman sudah
            # keluar dari level (lihat _putuskan_ambang_recovery).
            return _putuskan_ambang_recovery(target, n)
        try:
            # Percobaan 1-2 = perilaku lama, TIDAK berubah: reload tab
            # yang sama. Pemeriksaan URL asli baru dilakukan di ambang
            # 3x (_putuskan_ambang_recovery).
            state.PAGE.reload(timeout=25000)
            print(f"[RECOVERY] reload tab yang sama berhasil: "
                  f"{target.split('/')[-1]} (percobaan {n}/3)")
            state.last_typed_text = ""
            state._std_last_rem = None    # biar handle_standard mau ketik ulang
            state._std_attempts = 0
            state._lesson_anchor = target
            state.last_url = state.PAGE.url
            state.last_action_time = time.time()
            return True
        except Exception as e:
            print(f"[RECOVERY] reload gagal ({str(e)[:60]}) - coba tab baru")

    print("[RECOVERY] Halaman macet/kosong, membuka tab baru...")
    try:
        newpg = browser._tab_latar(state.PAGE.context)
    except Exception as e:
        print(f"[RECOVERY] gagal bikin tab: {str(e)[:80]}")
        return False

    if target:
        try:
            newpg.goto(target, timeout=25000)
            print(f"[RECOVERY] muat ulang lesson yang sama: {target.split('/')[-1]}")
            # tab baru mendarat di level yang sama = level sesi masih ini
            state._lesson_anchor = target
        except Exception as e:
            print(f"[RECOVERY] gagal muat ulang: {str(e)[:80]}")
            try:
                newpg.close()
            except Exception:
                pass
            return False
    else:
        # Rentang aktif: kembali ke level sesi (max start vs yang sudah
        # dilihat), bukan baris pertama di daftar (= level terdepan akun,
        # ). Fallback: baris pertama seperti biasa.
        lanjut_lvl = 0
        try:
            if state.RANGE_READY and (state.LEVEL_START > 1 or state.LEVEL_END):
                lanjut_lvl = max(state.LEVEL_START, state._range_max_seen)
        except Exception:
            lanjut_lvl = 0
        if lanjut_lvl and str(lanjut_lvl) in state._level_map:
            try:
                newpg.goto(state._level_map[str(lanjut_lvl)], timeout=25000)
                print(f"[RECOVERY] kembali ke level {lanjut_lvl} sesi ini")
                _finish_recovery(newpg)
                return True
            except Exception as e:
                print(f"[RECOVERY] gagal kembali ke level {lanjut_lvl} "
                      f"({str(e)[:60]}) - coba daftar")
                try:
                    newpg.close()
                    newpg = browser._tab_latar(state.PAGE.context)
                except Exception:
                    return False
        try:
            newpg.goto(LIST_URL, timeout=20000)
        except Exception as e:
            print(f"[RECOVERY] gagal buka daftar: {str(e)[:80]}")
            try:
                newpg.close()
            except Exception:
                pass
            return False
        try:
            newpg.wait_for_selector("div.lsn_name", timeout=15000)
        except Exception:
            pass
        clicked = None
        try:
            clicked = newpg.evaluate("""
            () => {
                const rows = document.querySelectorAll('div.box-container.is_unlocked:not(.has_progress)');
                for (const r of rows) {
                    const nm = r.querySelector('div.lsn_name');
                    if (nm) { nm.click(); return (nm.innerText||'').trim(); }
                }
                const any = document.querySelector('div.lsn_name');
                if (any) { any.click(); return '(pertama) ' + (any.innerText||'').trim(); }
                return null;
            }
            """)
        except Exception:
            pass
        if not clicked:
            print("[RECOVERY] baris pelajaran tidak ditemukan")
            try:
                newpg.close()
            except Exception:
                pass
            return False
        print(f"[RECOVERY] membuka pelajaran: {clicked}")

    _finish_recovery(newpg)
    return True


def _finish_recovery(newpg):
    """ tunggu tab recovery siap, jadikan tab utama, tutup tab lama """
    for _ in range(15):
        try:
            newpg.wait_for_timeout(1000)
        except Exception:
            time.sleep(1)
        if ".play" in newpg.url:
            break
    try:
        old = state.PAGE
        state.PAGE = newpg
        if old is not newpg:
            old.close()
    except Exception:
        pass
    state.last_typed_text = ""
    try:
        state.last_url = state.PAGE.url
    except Exception:
        state.last_url = ""
    asli = _url_asli_sekarang(newpg)
    if asli and browser._di_lesson(asli):
        state._lesson_anchor = browser._lesson_play_url(asli)
        state._recovery_pending_lesson = ""
    state.last_action_time = time.time()
    return True
