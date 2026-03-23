# YILDIZ Ders Otomasyonu

Yıldız Teknik Üniversitesi online derslerine otomatik katılım sağlayan hafif ve hızlı uygulama.

## Özellikler

- **Selenium-free**: Tarayıcı gerektirmez, sadece HTTP API kullanır
- **Hafif**: Düşük kaynak tüketimi, hızlı başlatma
- **Otomatik katılım**: Ders saati geldiğinde Zoom otomatik açılır
- **API tabanlı ders çekme**: Derslerinizi ve saatlerini otomatik algılar
- **Çoklu platform**: macOS ve Windows desteği

## Gereksinimler

1. **Python 3.8+**
   - macOS: `brew install python` veya [python.org](https://www.python.org/downloads/)
   - Windows: [python.org](https://www.python.org/downloads/) (Kurulumda "Add to PATH" işaretleyin)

2. **Zoom Desktop**
   - [zoom.us/download](https://zoom.us/download)

## Kurulum ve Çalıştırma

### macOS

```bash
./run_macos.command
```

Veya Finder'da `run_macos.command` dosyasına çift tıklayın.

### Windows

`run_windows.bat` dosyasına çift tıklayın.

> **Not:** İlk çalıştırmada otomatik olarak virtual environment oluşturulur ve bağımlılıklar yüklenir.

## Kullanım

### 1. Giriş

- Okul mailinizi girin (örnek: `ogrenci@std.yildiz.edu.tr`)
- Şifrenizi girin
- "Bilgileri Kaydet" tıklayın

### 2. Ders Ekleme

**Otomatik (Önerilen):**
1. "Derslerimi Getir (API)" tıklayın
2. Derslerinizi seçin
3. "Seçilenleri Ekle" tıklayın
4. Saatler otomatik eklenir

**Manuel:**
1. Gün seçin
2. Saat girin (örnek: 09:30)
3. Ders adını girin
4. "Ders Ekle" tıklayın

### 3. Otomatik Katılım

Uygulama açık kaldığı sürece arka planda çalışır. Ders saati geldiğinde otomatik olarak Zoom açılır.

### 4. Anlık Katılım

"Şimdi Derse Gir" butonu ile aktif derse hemen katılabilirsiniz.

## Sorun Giderme

| Sorun | Çözüm |
|-------|-------|
| "Login başarısız" | Kullanıcı adı/şifre kontrol edin |
| "Ders bulunamadı" | Ders saatinde olduğunuzdan emin olun |
| Zoom açılmıyor | Zoom Desktop yüklü mü kontrol edin |

## Dosya Yapısı

```
main.py              - Ana uygulama (GUI)
ytu_client.py        - YTU Online API istemcisi
zoom_launcher.py     - Zoom protokol başlatıcı
config.py            - Yapılandırma ayarları
run_macos.command    - macOS başlatıcı
run_windows.bat      - Windows başlatıcı
schedule.json        - Ders programı (otomatik oluşur)
```

## Lisans

MIT License - Detaylar için [LICENSE](LICENSE) dosyasına bakın.

## Yazar

Mert Kaya
