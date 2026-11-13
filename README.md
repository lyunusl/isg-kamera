# İSG Kamera

İSG Kamera, güvenlik kameralarından ve video kayıtlarından iş sağlığı ve güvenliğiyle ilgili durumları incelemenize yardımcı olur. Baret veya yelek eksikliği, yasak bölgeye giriş ve araç–yaya yakınlığı gibi olayları tespit eder, uyarıları ve olay kliplerini bir panelde toplar.

<p align="center">
  <a href="https://github.com/lyunusl/isg-kamera/releases/latest/download/IsgKamera-Kurulum-0.1.0.exe">
    <img src="https://img.shields.io/badge/Windows%20i%C3%A7in%20indir-%C4%B0SG%20Kamera-0078D4?style=for-the-badge&logo=windows&logoColor=white" alt="Windows için İSG Kamera'yı indir" height="56">
  </a>
  <br>
  <sub>Windows 10/11 (64 bit) · <a href="https://github.com/lyunusl/isg-kamera/releases/latest">Sürüm notları</a></sub>
</p>

![Örnek olaylar ve kamera görüntüleriyle İSG Kamera kontrol odası](gorseller/kontrol-odasi.jpg)

*Kontrol Odası, örnek video analiziyle oluşturulmuş veriler gösteriyor.*

## Neler yapabilirsiniz?

- **Kamera veya video ekleyin.** IP kamera, kayıt cihazı, ONVIF cihazı, HTTP akışı veya video dosyasıyla çalışın.
- **İzlenecek bölgeleri çizin.** Makine çevresi, yasak bölge veya araç yolu gibi alanları görüntü üzerinde belirleyin.
- **Kuralları seçin.** Her kamera için izlemek istediğiniz koruyucu ekipman, alan, davranış ve araç kurallarını ayarlayın.
- **Uyarıları inceleyin.** Olayları risk düzeyi, kamera ve durumlarına göre filtreleyin. Olayın karesini ve oluştuğunda klibini açın.
- **Takip ve raporlama yapın.** Uyarılardan aksiyon oluşturun, görsel analitiği inceleyin ve Excel raporu alın.
- **Çıkışları yapılandırın.** Modbus TCP, OPC UA, MQTT, webhook ve e-posta gibi bağlantılarla olay bilgilerini aktarın.

![İSG Kamera uyarı listesi](gorseller/uyarilar.jpg)

*Örnek videolardan oluşan uyarılar, kamera ve risk bilgileriyle listelenir.*

## Neleri algılar?

| Kategori | Örnekler |
|---|---|
| Koruyucu ekipman | Baret, reflektif yelek, eldiven ve koruyucu gözlük eksikliği |
| Alan kontrolü | Yasak bölgeye giriş, makine alanında yetkisiz kişi, alanda fazla veya eksik kişi |
| Araçlar | Araç–yaya yakınlığı, ters yönde sürüş ve araç yolundaki engeller |
| Davranış ve ergonomi | Koşma, tırmanma, düşen kişi ve yaklaşık duruş değerlendirmesi |

![İSG Kamera kural kataloğu](gorseller/kurallar.jpg)

*Kurallar ekranında her kuralın açıklaması ve sınama durumu görülebilir.*

![Örnek videoda yasak bölge ve koruyucu ekipman uyarısı](gorseller/ornek-tespit.jpg)

*Yapay zekâ ile oluşturulmuş örnek videoda işaretlenen alan ve tespit edilen olay.*

## Nasıl çalışır?

1. Kamera akışını veya video dosyasını ekleyin.
2. Görüntü üzerinde bölgeleri çizin ve izlenecek kuralları seçin.
3. Analizi başlatın. Görüntüyü bilgisayarınızdaki **AugeLab Studio** arka planda işler; Studio penceresi açılmaz.
4. Oluşan uyarıları, görselleri ve raporları İSG Kamera panelinde inceleyin.

Programda dört örnek video ve hazır kamera ayarları bulunur. Önce örnekleri çalıştırarak paneli deneyebilirsiniz.

## Kurulum

Yukarıdaki **Windows için indir** düğmesiyle kurulum dosyasını indirip çalıştırın. Kurulum adım adım ilerler:

1. **İSG Kamera** bu kullanıcı için kurulur (yönetici izni gerekmez).
2. **AugeLab Studio** kurulu değilse resmî kurulum dosyası indirilir, doğrulanır ve sessizce kurulur. Visual C++ bileşeni eksikse Microsoft'tan kurulur; Windows izin sorabilir.
3. **AugeLab hesabı**: hesabınız yoksa düğmeyle tarayıcıda oluşturun. Ücretsiz Community planı yeterlidir.
4. **Lisans kodu**: hesabınızdaki lisans kodunu yapıştırın. Kod, Studio penceresi açılmadan Studio'ya kaydedilir; İSG Kamera onu saklamaz.

Kurulum bitince İSG Kamera açılır ve panel tarayıcınızda görünür. Studio'nun hazır olduğu kısa bir arka plan denemesiyle doğrulanır. Bir adımı atladıysanız paneldeki kurulum sihirbazı aynı adımları gösterir. Ardından **Ayarlar** bölümünden örnek analizi çalıştırın veya kamera ekleyin.

Analizi, arayüzsüz kullanıma izin veren bir AugeLab Studio lisansıyla çalıştırabilirsiniz. Lisans doğrulaması analiz sırasında Studio tarafından yapılır. İlk kurulum ve lisans etkinleştirme için internet gerekir. Studio, lisansınızın FPS sınırını uygular.

## Kamera ve veri

ONVIF destekli cihazlar ağda aranabilir, kayıt cihazlarındaki kanallar ayrı kameralar olarak eklenebilir. RTSP adresi elle de girilebilir. Şifre ayar dosyasına kaydedilmez.

Analiz bilgisayarınızda yapılır. Olayların kare ve kliplerinde **algılanan kişilerin yüzleri gizlenir**. Yüz gizleme kişi tespitine bağlıdır; algılanmayan bir kişinin yüzü açık kalabilir. Olay kayıtları bilgisayarda saklanır ve tanımladığınız bildirim çıkışlarına olay bilgileri gönderilebilir.

## Sınırlar ve test durumu

- Bu sürüm geliştirme aşamasındadır. Örnek klipler yapay zekâ ile üretilmiştir ve gerçek kamera kayıtlarıyla doğrulanmamıştır.
- Gerçek IP kamera, ONVIF cihazı ve kayıt cihazıyla uçtan uca test yapılmamıştır. ONVIF akışı yerel bir test cihazıyla sınanmıştır.
- Makine örneği kurulu Studio'da başarıyla işlendi. Depo örneğinde Studio sahnesi erken durdu. Son sürümün diğer örnekler ve temiz Windows ortamındaki analizi doğrulanmayı bekliyor.
- Bazı kurallar sınırlı sonuç verir; emniyet kemeri ve kulak koruyucu gibi kuralların ayrıntıları panelde gösterilir. RULA kuralı henüz etkin değildir.
- Aynı anda bir kamera izlenir. USB kamera bu sürümde desteklenmez; IP kamera, kayıt cihazı veya video dosyası kullanın.
- Siemens S7 çıkışı bu sürümde yoktur. Modbus TCP, MQTT, OPC UA, webhook ve e-posta çıkışlarını panel gönderir.
- Bildirim ve kontrol çıkışları ikaz ve entegrasyon içindir. Emniyet durdurması olarak kullanılmaz.

## Kaynak koddan geliştirme

Python 3.12 ve Windows x64 üzerinde, proje klasöründe ortamı hazırlayın.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install --target motor\deps --no-deps onnxruntime==1.31.1
New-Item -ItemType Directory -Force motor\modeller | Out-Null
Copy-Item kurulum\community-modeller\* motor\modeller\
.venv\Scripts\python uygulama.py
```

Panel `http://127.0.0.1:8780` adresinde açılır. AugeLab Studio kurulu ve lisanslı olmalıdır; panel Studio'yu açmaz, her kamera için `python -m studio run` ile arka planda çalıştırır.

Çalıştırılan hazır sahne (`kurulum\sahne\isg-kamera.pmod`) depoda yoktur, çünkü Studio sahne dosyalarını şifreli kaydeder ve kaydetme yalnızca Studio penceresinden yapılır. Bir kez üretilir:

1. Studio'yu açın. MCP `127.0.0.1:8765` adresinde açık olmalıdır.
2. `.venv\Scripts\python kurulum\sablon.py` çalıştırın.
3. Studio'da açılan sekmeyi **Dosya → Farklı Kaydet** ile `kurulum\sahne\isg-kamera.pmod` olarak kaydedin.

EXE ve Windows kurulum dosyası oluşturmak için PyInstaller ve Inno Setup ile `kurulum\derle.cmd` çalıştırılır.

## Lisans ve kaynaklar

İSG Kamera'nın kodu GNU AGPL-3.0 lisanslıdır. AugeLab Studio ayrı lisanslı bir üründür. [Uygulama lisansı](LICENSE) · [Windows paketi atıfları](ATIFLAR.md) · [Model lisans bildirimleri](MODEL-LISANSLARI.md)
