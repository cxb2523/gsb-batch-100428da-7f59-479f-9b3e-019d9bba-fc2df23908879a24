import csv
import json
import os
import tempfile

from . import konstanta


def _tulis_atomik(path: str, tulis) -> str:
    """
    Menulis berkas secara atomik memakai berkas sementara di direktori sama.

    ``tulis`` adalah callable yang menerima file object dan menulis isinya.
    Bila penulisan atau pemindahan gagal (termasuk KeyboardInterrupt), berkas
    sementara dibersihkan dan path tujuan tidak tersentuh.

    Args:
        path: Path berkas tujuan.
        tulis: Callable(file_object) -> None.

    Returns:
        Path berkas yang berhasil ditulis.
    """
    direktori = os.path.dirname(os.path.abspath(path))
    os.makedirs(direktori, exist_ok=True)

    fd, path_sementara = tempfile.mkstemp(prefix=".tmp_", dir=direktori)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            tulis(f)
        os.replace(path_sementara, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(path_sementara)
        except OSError:
            pass
        raise

    return path


def tulis_csv(nama_file: str, baris: list, header: list | None = None) -> str:
    """
    Menulis daftar baris ke file CSV secara atomik.

    Penulisan dilakukan ke file sementara di direktori yang sama lalu
    dipindahkan dengan ``os.replace``. Dengan begitu, jika proses terhenti
    di tengah penulisan (misalnya Ctrl+C), file hasil sebelumnya tidak
    rusak dan hanya berisi sebagian data.

    Args:
        nama_file: Path file CSV tujuan.
        baris: Daftar baris (iterable) yang akan ditulis.
        header: Baris header. Default: konstanta.HEADER_CSV.

    Returns:
        Path file yang berhasil ditulis.
    """
    if header is None:
        header = konstanta.HEADER_CSV

    def tulis(f) -> None:
        penulis = csv.writer(f)
        penulis.writerow(header)
        penulis.writerows(baris)

    return _tulis_atomik(nama_file, tulis)


def path_cookie_sementara(direktori_dasar: str | None = None) -> str:
    """
    Mengembalikan path berkas cookie sementara di dalam direktori cookies/.

    Lokasinya relatif terhadap direktori kerja (sama seperti folder data/),
    sehingga direktori kerja tetap bersih dari berkas sementara.

    Args:
        direktori_dasar: Direktori dasar opsional; default direktori kerja.

    Returns:
        Path berkas cookie sementara.
    """
    if direktori_dasar is None:
        direktori_dasar = os.getcwd()
    return os.path.join(direktori_dasar, konstanta.DIREKTORI_COOKIES,
                        konstanta.NAMA_COOKIE_SEMENTARA)


def tulis_cookie_sementara(storage_state: dict,
                           path: str | None = None,
                           direktori_dasar: str | None = None) -> str:
    """
    Menulis storage state cookie ke berkas sementara di cookies/.

    Penulisan memakai file sementara + ``os.replace`` agar interrupt di
    tengah penulisan tidak meninggalkan JSON setengah jadi.

    Args:
        storage_state: Dict storage state yang kompatibel dengan Playwright.
        path: Path tujuan opsional; default cookies/temp_cookie_state.json.
        direktori_dasar: Direktori dasar opsional jika path tidak diberikan.

    Returns:
        Path berkas yang ditulis.
    """
    if path is None:
        path = path_cookie_sementara(direktori_dasar)

    def tulis(f) -> None:
        json.dump(storage_state, f)

    return _tulis_atomik(path, tulis)


def hapus_cookie_sementara(path: str | None = None,
                           direktori_dasar: str | None = None) -> bool:
    """
    Menghapus berkas cookie sementara jika ada.

    Aman dipanggil dari blok finally pada jalur keluar mana pun. Tidak
    pernah melempar exception sehingga tidak menutupi exception asli.

    Args:
        path: Path berkas opsional; default cookies/temp_cookie_state.json.
        direktori_dasar: Direktori dasar opsional jika path tidak diberikan.

    Returns:
        True jika ada berkas yang dihapus, False jika tidak ada.
    """
    if path is None:
        path = path_cookie_sementara(direktori_dasar)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def konversi_cookie_string(cookie_string: str) -> dict:
    """
    Mengonversi cookie dalam format string mentah ke format JSON
    yang dapat digunakan oleh Playwright.

    Args:
        cookie_string: Cookie yang disalin dari browser dalam bentuk string.

    Returns:
        Sebuah dictionary yang berisi daftar cookies untuk state browser.
    """
    cookies = []
    if not cookie_string:
        return {"cookies": [], "origins": []}
    
    pasangan_cookie = cookie_string.split(";")

    for pasangan in pasangan_cookie:
        if "=" in pasangan:
            nama, nilai = pasangan.strip().split("=", 1)

            cookie = {
                "name": nama.strip(),
                "value": nilai.strip(),
                "domain": ".instagram.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "None"
            }

            if nama.strip() in ['sessionid', 'csrftoken']:
                cookie["httpOnly"] = True
            
            cookies.append(cookie)
    
    return {
        "cookies": cookies,
        "origins": [
            
        ]
    }