# ResiTrack — Multi-Courier Shipment Tracker (Shopee Integrated)

Aplikasi desktop Windows modern & clean untuk melacak resi **SPX (Shopee Express), JNE, POS Indonesia, dan Anteraja**, serta menghubungkan data resi dengan **Username Buyer Shopee** dan **Status Pesanan**.

---

## 1. Taksonomi Status Cerdas (Lacak Resi + Lacak Pesanan)

Sistem mengidentifikasi status gabungan antara **Lacak Resi Ekspedisi** dan **Lacak Pesanan Shopee**:

| Status | Makna & Indikator Identifikasi |
|---|---|
| **Menunggu Pickup** | Label pengiriman telah dibuat / pesanan disiapkan, kurir belum menjemput paket. |
| **Dalam Perjalanan** | Paket sedang bergerak transit antar gudang / hub sortir / line-haul. |
| **Sedang Diantar** | Kurir sedang ditugaskan mengantar langsung ke pintu pembeli (*out for delivery* / *driver assigned to deliver*). |
| **Terkirim** | Paket telah sampai dan diterima pembeli (*delivered*), namun status pesanan di Shopee belum selesai (garansi Shopee masih aktif / dana belum cair). |
| **Selesai** | Status pesanan Shopee sudah **Selesai / Completed** (dana penjualan sudah dicairkan ke saldo penjual). |
| **Pengembalian** | Kategori RTS: Proses retur barang / pengembalian dana resmi dari pembeli (*Return/Refund*). |
| **Pengiriman Gagal - Ditolak Pembeli** | Kategori RTS: Kurir **sudah pernah ditugaskan mengantar ke pintu pembeli** (*delivery attempt*), namun paket ditolak (misal: COD ditolak pembeli / rumah kosong berkali-kali). |
| **Pengiriman Gagal - BalSis** | Kategori RTS: **Batal Sistem**! Paket belum sampai pembeli, kurir **BELUM PERNAH ditugaskan mengantar ke pembeli** (masih di gudang sortir/hub transit), tapi sudah RTS (rusak, hilang di sortir, salah sortir, atau dibatalkan sistem). **Cocok untuk klaim asuransi/ganti rugi ke Shopee/Ekspedisi.** |
| **Canceled** | Pesanan dibatalkan sebelum dikirim atau dibatalkan oleh sistem Shopee. |

---

## 2. Fitur Tampilan Modern (Clean & Minimalist)

1. **Dashboard KPI Chips Interaktif**:
   - Di bagian atas terdapat kartu statistik per status (Menunggu Pickup, Sedang Diantar, Terkirim, Selesai, BalSis, Ditolak Pembeli, dll).
   - Klik kartu status untuk langsung memfilter tabel sesuai status tersebut.
2. **Pill Badges Berwarna**:
   - Status di tabel ditampilkan sebagai lencana pill rounded modern dengan warna khas (*emerald* untuk Selesai, *amber* untuk Sedang Diantar, *crimson red* untuk BalSis, dll).
3. **Panel Diagnosis Khusus (Sidebar Kanan)**:
   - Menampilkan alert box khusus saat paket terdeteksi **BalSis** (lengkap dengan panduan klaim kompensasi), **Ditolak Pembeli**, atau **Pesanan Selesai**.
   - Stepper timeline vertikal yang rapi dengan checkpoint waktu, deskripsi, dan lokasi hub.
4. **Pencarian Cepat**:
   - Filter instan berdasarkan username pembeli, nomor resi, no. pesanan, nama penerima, kota, atau produk.

---

## 3. Integrasi Endpoint Ekspedisi Komunitas (Tanpa Biaya / API Resmi)

- **SPX Express**: Endpoint internal publik `spx.co.id` + Gateway Komunitas.
- **Anteraja**: Endpoint REST API Android `api.anteraja.id` + Gateway Komunitas.
- **JNE & POS**: Menggunakan gateway logistik komunitas open-source ZPI (dari repo `fk0u/cekresi`) yang aktif tanpa perlu mendaftar/membayar API key resmi, plus fallback BinderByte jika user memiliki key pribadi.
- **Shopee Open API**: Terhubung ke endpoint `v2.order.get_order_detail` dan `v2.logistics.get_tracking_info` saat toko sudah diotorisasi.

---

## 4. Cara Menjalankan

- **Buka Cepat dari Desktop**: Klik 2x shortcut **`ResiTrack`** di Desktop depan Windows Anda.
- **Jalankan via Script**: Klik file **`run.bat`**.
- **Build ke Standalone `.exe`**: Klik file **`build.bat`** (hasil di folder `dist\ResiTrack.exe`).
