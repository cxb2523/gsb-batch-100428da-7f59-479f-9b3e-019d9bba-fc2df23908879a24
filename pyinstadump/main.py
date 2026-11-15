import argparse
import sys
import os
import logging

from .utilitas import (
    konversi_cookie_string,
    tulis_cookie_sementara,
    hapus_cookie_sementara,
)
from .pengikis import PengikisInstagram
from .konstanta import DAFTAR_TERLARANG_DEFAULT, DIREKTORI_DATA

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def main() -> int:
    """
    Fungsi utama untuk menjalankan aplikasi dari baris perintah.

    Returns:
        Kode keluar: 0 jika data tersimpan ke CSV final, 1 jika tidak ada
        data atau terjadi kegagalan, 130 jika dihentikan pengguna.
    """
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass

    os.system('cls' if os.name == 'nt' else 'clear')
    banner = r"""
╭────────────────────────────────────────────────────────────────────────────────────────────╮
│  _______        _____                  _          ______                                   │
│ |_   __ \      |_   _|                / |_       |_   _ `.                                 │
│   | |__) |_   __ | |   _ .--.   .--. `| |-',--.    | | `. \ __   _   _ .--..--.  _ .--.    │
│   |  ___/[ \ [  ]| |  [ `.-. | ( (`\] | | `'_\ :   | |  | |[  | | | [ `.-. .-. |[ '/'`\ \  │
│  _| |_    \ '/ /_| |_  | | | |  `'.'. | |,// | |, _| |_.' / | \_/ |, | | | | | | | \__/ |  │
│ |_____| [\_:  /|_____|[___||__][\__) )\__/\'-;__/|______.'  '.__.'_/[___||__||__]| ;.__/   │
│          \__.'                                                                  [__|       │
│    PyInstaDump - Scraper Instagram dengan Playwright • Versi: 1.0.0 • Penulis: Rozhak      │
╰────────────────────────────────────────────────────────────────────────────────────────────╯
    """ # Coded by Rozhak

    print(banner)

    parser = argparse.ArgumentParser(
        description="Scraper data followers/following Instagram menggunakan Playwright.",
        epilog="Contoh: python -m pyinstadump.main --username lentaripagi --mode following --cookie \"<cookie_string_anda>\""
    )
    parser.add_argument(
        "-u", "--username",
        required=True,
        help="Username target yang akan di-scrape."
    )
    parser.add_argument(
        "-m", "--mode",
        choices=['followers', 'following'],
        required=True,
        help="Mode scraping: 'followers' atau 'following'."
    )
    parser.add_argument(
        "-c", "--cookie",
        required=True,
        help="Cookie sesi Instagram dalam format string. Penting untuk otentikasi."
    )
    parser.add_argument(
        "-o", "--output",
        help="Path file output CSV. Default: data/[mode]_[username].csv"
    )

    args = parser.parse_args()

    if args.username.lower() in [nama.lower() for nama in DAFTAR_TERLARANG_DEFAULT]:
        logging.critical(f"Kesalahan: Username '{args.username}' termasuk dalam daftar akun yang dilindungi.")
        logging.critical("Program dihentikan untuk melindungi akun tersebut.")
        return 1

    if not args.output:
        os.makedirs(DIREKTORI_DATA, exist_ok=True)
        args.output = os.path.join(DIREKTORI_DATA, f"{args.mode}_{args.username}.csv")

    kode_keluar = 0
    pengikis = None
    try:
        logging.info("Mengonversi cookie string untuk sesi browser...")
        storage_state = konversi_cookie_string(args.cookie)

        # Berkas cookie sementara berada di cookies/ dan dibersihkan di
        # finally, sehingga jalur keluar mana pun (normal, error, Ctrl+C)
        # tidak meninggalkan berkas di direktori kerja.
        cookie_file_path = tulis_cookie_sementara(storage_state)

        logging.info(f"Memulai scraping untuk '{args.username}' pada mode '{args.mode}'.")

        pengikis = PengikisInstagram(
            target_username=args.username,
            mode_kikis=args.mode,
            file_cookie=cookie_file_path
        )
        pengikis.set_output_file(args.output)

        hasil = pengikis.jalankan()

        if hasil:
            if pengikis.simpan_ke_csv(args.output):
                logging.info(f"Scraping selesai! Data disimpan ke {args.output}")
            else:
                # Gagal menulis CSV final: jangan buang data, simpan parsial.
                logging.warning("Gagal menulis CSV final; menyimpan hasil parsial.")
                pengikis.simpan_hasil_parsial()
                kode_keluar = 1
        else:
            logging.warning(
                "Tidak ada data yang berhasil diekstrak untuk disimpan. "
                f"Target: '{args.username}', mode: '{args.mode}'. "
                "Tidak ada file CSV final maupun file parsial yang dibuat."
            )
            kode_keluar = 1
    except KeyboardInterrupt:
        kode_keluar = 130
        logging.warning("Program dihentikan oleh pengguna (Ctrl+C).")
        if pengikis is not None:
            _simpan_parsial_bila_belum(pengikis)
    except Exception as e:
        kode_keluar = 1
        logging.critical(f"Proses gagal dengan kesalahan fatal: {e}")
        if pengikis is not None:
            _simpan_parsial_bila_belum(pengikis)
    finally:
        if hapus_cookie_sementara():
            logging.info("Berkas cookie sementara di cookies/ dihapus.")
        logging.info("Proses selesai.")

    return kode_keluar


def _simpan_parsial_bila_belum(pengikis: PengikisInstagram) -> None:
    """
    Jaring pengaman penyimpanan data parsial untuk jalur error/interrupt.

    jalankan() sudah menyimpan data parsial sebelum meneruskan exception,
    sehingga fungsi ini hanya menulis ulang jika belum ada penyimpanan
    yang berhasil (misalnya interrupt terjadi saat inisialisasi).

    Args:
        pengikis: Objek scraper yang mungkin sudah memiliki data.
    """
    if pengikis.status_akhir == "parsial_tersimpan":
        return
    if not pengikis.hasil_scrape:
        logging.warning(
            f"Tidak ada data yang terkumpul untuk target "
            f"'{pengikis.target_username}' mode '{pengikis.mode_kikis}'; "
            f"tidak ada file parsial yang dibuat."
        )
        return
    pengikis.simpan_hasil_parsial()


if __name__ == "__main__":
    sys.exit(main())