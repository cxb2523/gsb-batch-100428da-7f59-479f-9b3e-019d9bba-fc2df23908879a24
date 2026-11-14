# 🛒 Shopee Scraper Extension v1.0.0

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Chrome Extension](https://img.shields.io/badge/Chrome_Extension-Manifest_V3-orange.svg)](https://developer.chrome.com/docs/extensions/mv3/intro/)

Chrome extension ultra premium untuk scraping komentar/ulasan produk dari Shopee Indonesia dengan desain Clean Light Mode, analisis statistik real-time (Positif, Netral, Negatif), suite tombol lengkap, integrasi GitHub repository, dan ekspor ke format CSV.

---

## 🔗 Repository GitHub

Repository resmi: [https://github.com/Tnembull/shopee-scraper-extension.git](https://github.com/Tnembull/shopee-scraper-extension.git)

```bash
git clone https://github.com/Tnembull/shopee-scraper-extension.git
```

---

## 📋 Fitur Utama

- 🎨 **Desain Clean Light Mode**: Tampilan light mode modern, tipografi Inter, dan statistik dashboard real-time.
- 🚀 **Tombol Scraping & Hentikan Instan**: Mulai scraping dan hentikan proses kapan saja (*Abort Controller*) tanpa kehilangan ulasan yang sudah diambil.
- 📊 **Dashboard Analisis & Filter Rating**:
  - Statistik real-time total ulasan, jumlah Positif (4-5★), Netral (3★), dan Negatif (1-2★).
  - Filter tampilan & ekspor: *Semua*, *Positif*, *Netral*, atau *Negatif*.
- 📥 **Export to CSV & Smart Naming**: Ekspor CSV otomatis dengan kolom `Kategori`, BOM UTF-8 (Excel friendly), dan penamaan file pintar sesuai nama produk.
- 📋 **Copy CSV to Clipboard**: Salin data ulasan langsung ke clipboard hanya dengan satu klik.
- 🐙 **Integrasi GitHub**: Tombol cepat untuk membuka repository GitHub langsung dari ekstensi.
- 👀 **Live Review Preview**: Intip 3 ulasan terbaru secara real-time langsung di UI popup.
- ⚡ **Auto Content Script Injection**: Script otomatis ter-inject jika ekstensi dibuka di tab Shopee yang belum teregister.
- 📦 **Multi-Chunk Auto-Save**: Otomatis menyimpan file CSV per 100 halaman (`CHUNK_SIZE`) untuk scraping data skala besar.
- ♻️ **Checkpoint & Lanjutkan (Resumable)**: Progress setiap chunk dicatat di `chrome.storage.local` (nomor chunk, daftar chunk yang sudah diekspor beserta jumlah komentar & rentang halamannya, halaman lanjutan, dan alasan berakhirnya proses). Jika scraping terhenti karena tombol paginasi gagal atau pengguna menekan **Hentikan Process**, run berikutnya pada produk yang sama melanjutkan dari halaman berikutnya — chunk yang sudah diekspor tidak akan diekspor dua kali.
- 🛟 **Tidak Ada Data Hilang**: Saat paginasi gagal atau proses dihentikan, komentar yang sudah terkumpul pada chunk berjalan tetap diekspor sebagai file `*_partial_*.csv` dan chunk tersebut **tidak** ditandai selesai.

---

## 🚀 Instalasi & Penggunaan

1. **Buka Chrome Extensions**
   - Ketik `chrome://extensions` di address bar Chrome.
   - Aktifkan **"Developer mode"** di pojok kanan atas.

2. **Load Extension**
   - Klik **"Load unpacked"**.
   - Pilih folder `shopee-scraper-extension`.

3. **Cara Menggunakan**
   - Buka halaman produk Shopee di browser.
   - Klik ikon **Shopee Scraper Extension** di toolbar extension.
   - Tentukan jumlah halaman ulasan (1-500).
   - Klik **🚀 Mulai Scraping**.
   - Gunakan tombol **⛔ Hentikan Process**, **📥 Export CSV**, **📋 Copy CSV**, atau **🐙 GitHub** sesuai kebutuhan.

---

## 🔘 Suite Tombol (Button Suite)

| Tombol | Fungsi |
|---|---|
| `🚀 Mulai Scraping` | Memulai proses pengambilan ulasan dari halaman produk |
| `⛔ Hentikan Process` | Menghentikan scraping secara seketika dan menyimpan ulasan yang ada |
| `📥 Export CSV` | Mengunduh file CSV dengan penamaan nama produk |
| `📋 Copy CSV` | Menyalin teks CSV ke clipboard |
| `🐙 Buka Repository GitHub` | Membuka link repository GitHub di tab baru |
| `🗑️ Reset / Hapus Data` | Membersihkan memori & reset form |
| `Filter Pills` | Memilih subset data: Semua, Positif (4-5★), Netral (3★), atau Negatif (1-2★) |

---

## 📊 Format Output CSV

CSV file menggunakan encoding `UTF-8 with BOM` agar emoji & simbol khusus tampil rapi di MS Excel:

| Username | Rating | Kategori | Date | Comment |
|----------|--------|----------|------|---------|
| user123 | 5 | Positif | 2 hari yang lalu | Barang bagus, sesuai deskripsi... |
| buyer456 | 1 | Negatif | 1 minggu yang lalu | Pengiriman lambat, barang penyok... |

---

## 🧪 Pengujian (Node.js, tanpa dependency)

Logika chunk, checkpoint, dan ekspor CSV diuji dengan `node:test` memakai komentar buatan serta stub `chrome.storage.local` — tidak memerlukan halaman Shopee asli:

```bash
cd chrome-extension
npm test          # menjalankan seluruh file tests/*.test.js
npm run check     # syntax check content.js, popup.js, background.js
```

### Format checkpoint (`chrome.storage.local` → `scrapeCheckpoint`)

| Field | Arti |
|---|---|
| `currentChunk` | Nomor chunk yang sedang/akan dikerjakan |
| `nextPage` | Halaman pertama yang harus diambil saat melanjutkan |
| `lastCompletedPage` | Halaman terakhir yang komentarnya sudah terekstrak |
| `exportedChunks[]` | `{ chunkNumber, commentCount, startPage, endPage, partial, filename }` untuk setiap CSV yang sudah diunduh |
| `endedEarly` / `endReason` | `pagination_failed` (tombol hilang / komentar tidak bertambah) atau `user_stopped`; `null` bila selesai normal |
| `completed` | `true` hanya bila seluruh halaman diminta sudah selesai |
| `productKey` / `maxPages` | Resume hanya berlaku bila produk dan jumlah halaman cocok |

Tombol **🗑️ Reset / Hapus Data** juga menghapus checkpoint ini.

---

## 🤝 Kontribusi

Kontribusi dan Pull Requests selalu terbuka! Silakan baca panduan [CONTRIBUTING.md](CONTRIBUTING.md) untuk mulai berkontribusi.

---

## 👨‍💻 Author

Dibuat dengan ❤️ oleh **[Tnembull](https://github.com/Tnembull)**.

---

## 📄 Lisensi & Disclaimer

Project ini berlisensi di bawah **[MIT License](LICENSE)**.

```
Copyright (c) 2026 Tnembull
```

*Disclaimer: Project ini dibuat untuk tujuan edukasi dan riset. Harap selalu mematuhi Syarat & Ketentuan serta robots.txt Shopee. Gunakan dengan bijak.*