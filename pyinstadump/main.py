import argparse
import sys
import os
import logging

from .utilitas import konversi_cookie_string
from .pengikis import PengikisInstagram
from .konstanta import DAFTAR_TERLARANG_DEFAULT

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def main() -> None:
    """
    Fungsi utama untuk menjalankan aplikasi dari baris perintah.
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
        sys.exit(1)

    if not args.output:
        if not os.path.exists('data'):
            os.makedirs('data')
        args.output = os.path.join('data', f"{args.mode}_{args.username}.csv")
    
    logging.info("Mengonversi cookie string untuk sesi browser...")
    storage_state = konversi_cookie_string(args.cookie)

    cookie_file_path = "temp_cookie_state.json"
    import json
    with open(cookie_file_path, "w") as f:
        json.dump(storage_state, f)
    
    logging.info(f"Memulai scraping untuk '{args.username}' pada mode '{args.mode}'.")

    try:
        pengikis = PengikisInstagram(
            target_username=args.username,
            mode_kikis=args.mode,
            file_cookie=cookie_file_path
        )

        pengikis.set_output_file(args.output)

        hasil = pengikis.jalankan()

        if hasil:
            pengikis.simpan_ke_csv(args.output)
            logging.info(f"Scraping selesai! Data disimpan ke {args.output}")
        else:
            logging.warning("Tidak ada data yang berhasil diekstrak untuk disimpan.")
    except KeyboardInterrupt:
        logging.info("Program dihentikan oleh pengguna.")
    except Exception as e:
        logging.critical(f"Proses gagal dengan kesalahan fatal: {e}")
        logging.info("Data yang sudah dikumpulkan (jika ada) sudah disimpan otomatis.")
    finally:
        if os.path.exists(cookie_file_path):
            os.remove(cookie_file_path)
        logging.info("Proses selesai.")

if __name__ == "__main__":
    main()