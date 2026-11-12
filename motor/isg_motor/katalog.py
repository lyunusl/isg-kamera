"""Kural kataloğu: hangi tehlike nasıl tespit edilir.

Her kuralın bir "mekanizma"sı vardır (kural.py içindeki değerlendirici):

  kkd        kişide gereken koruyucu yok                      (sinif, kanit: olumsuz | yokluk | oznitelik, bolge_turu?)
  durus      iskeletten anlaşılan durum: yerde yatan kişi     (durus)
  ergonomi   iskeletten hesaplanan duruş puanı (REBA)         (esik)
  nesne      bölgede bekleyen, yasak ya da sahipsiz nesne      (siniflar, bolge_turu?, sahipsiz?)
  uc_nokta   merdivende el ve ayaklardan en az üçü merdivende değil   (bolge_turu)
  bolge      kişi ya da araç bir bölgeye girdi               (ozne, bolge_turu, saatler?, yetkili_renk?)
  sayi       bölgedeki kişi sayısı sınırın üstünde/altında    (en_cok? | en_az?)
  yakinlik   iki özne birbirine çok yakın                     (a, b, esik: kişi boyu cinsinden)
  hiz        kişi koşuyor                                     (esik: boy/sn)
  yol_disi   özne kendi yolunun dışında                       (ozne, bolge_turu)
  ters_yon   özne bölgenin yönüne karşı gidiyor               (ozne, bolge_turu)
  birlikte   aynı bölgede aynı anda kişi ve araç var          (bolge_turu)
  nesne      bölgede bekleyen/yasak nesne                     (siniflar, bolge_turu?, sahipsiz?)

Alanlar:
  onem   1-5: olursa ne kadar ağır (5 = ölüm ya da kalıcı sakatlık olasılığı)
  ciddi  True: ciddi yaralanma ya da ölüm potansiyeli olan olay sınıfı
  durum  "hazir": sınama sahnelerinin hepsinden geçti · "sinirli": çalışıyor ama sınamada kaçırdığı bir durum var (açıklamada yazılı)
         "zayif": sınamada güvenilir çıkmadı, yanlış alarm veriyor · "yok": kapalı (yazılmadı ya da çalışan bir model yok)
         Sınamalar yapay zekâ ile üretilmiş klip ve görüntülerle yapıldı.
"""
from __future__ import annotations

import os
import time

from . import kasa

KATEGORILER = {
    "kkd": "Kişisel koruyucu",
    "alan": "Alan kontrolü",
    "davranis": "Davranış",
    "arac": "Araç",
    "duzen": "Düzen",
    "ergonomi": "Ergonomi",
}

# bölge türleri (kamera ayarında çizilir)
BOLGE_TURLERI = {
    "kkd": "KKD zorunlu alan",
    "yasak": "Yasak / tehlikeli bölge",
    "makine": "Makine bölgesi",
    "elektrik": "Elektrik tehlikesi",
    "carpisma": "Çarpışma riski bölgesi",
    "sayim": "Kişi sayısı izlenen alan",
    "yaya_yolu": "Yaya yolu",
    "arac_yolu": "Araç yolu",
    "arac_yasak": "Araç giremez",
    "yuksek": "Yüksekte çalışma alanı",
    "tirmanma": "Tırmanılmaması gereken yer (raf, makine)",
    "gecit": "Açık tutulacak geçit",
    "merdiven": "Merdiven",
}

# nesne modelinin sınıfları → kural sınıf kodları
SINIF_ADLARI = {
    "baret": "baret", "yelek": "reflektif yelek", "eldiven": "eldiven", "gozluk": "koruyucu gözlük", "bot": "iş ayakkabısı",
    "onluk": "önlük", "maske": "yüz maskesi", "siper": "yüz siperi", "kulaklik": "kulak koruyucu", "kemer": "emniyet kemeri",
    "tulum": "koruyucu giysi", "kolluk": "kolluk", "solunum": "solunum koruyucu",
}


SINAMA_NOTU: dict[str, str] = {  # kişi öznitelik modelinin, eğitimde kullanılmayan gerçek fotoğraflardaki sonucu
    "onluk": "Gerçek fotoğraflarda önlüklü 106 kişiden 105'ini buldu; önlüksüz 491 kişiden 4'ünü önlüklü saydı.",
    "tulum": "Gerçek fotoğraflarda tulumlu 127 kişiden 120'sini buldu (ayrı stok fotoğraf kümesinde 18'de 11); tulumsuz 512 kişiden 5'ini tulumlu saydı.",
    "kolluk": "Gerçek kolluk fotoğrafı bulunamadı; yalnızca üretilmiş görüntülerle eğitildi. Kolluksuz 21 gerçek kişiden 3'ünü kolluklu saydı; dirseğe kadar uzanan eldiveni kolluk sayıyor.",
    "kulaklik": "Gerçek fotoğraflarda kulaklıklı 73 kişiden 62'sini buldu (kaçırdığı kişiye yanlış uyarı verir); kulaklıksız 333 kişiden 9'unu kulaklıklı saydı.",
}


def _kkd(kod, sinif, ad, onem, durum, kanit="olumsuz"):
    k = {"kod": kod, "ad": ad, "kategori": "kkd", "mekanizma": "kkd", "sinif": sinif, "kanit": kanit, "onem": onem,
         "ciddi": False, "sure_sn": 2.0, "durum": durum, "aciklama": f"KKD zorunlu alanda {SINIF_ADLARI[sinif]} takmayan kişi."}
    if kanit == "yokluk":  # model "takılmamış" demez; kişi net görünürken koruyucu hiç bulunmazsa eksik sayılır
        k.update(sure_sn=4.0, varsayilan_kapali=True, en_az_boy=150,  # kişi bundan kısa görünüyorsa (piksel) değerlendirilmez
                 aciklama=k["aciklama"] + " Ek modelle çalışır: kişi net görünürken koruyucu bulunamazsa eksik sayılır.")
    if kanit == "oznitelik":  # kişi kırpıntısına bakan sınıflandırıcı: "var" da "yok" da modelden gelir
        k.update(sure_sn=3.0, varsayilan_kapali=True,
                 aciklama=k["aciklama"] + " Kişi öznitelik modeliyle çalışır (kişi görüntüde en az 110 piksel boyundaysa); arkadan görünen kişide yanılabilir.")
    if kod in SINAMA_NOTU:
        k["aciklama"] += " " + SINAMA_NOTU[kod]
    return k


KURALLAR: list[dict] = [
    # ---- kişisel koruyucu (12)
    _kkd("baret", "baret", "Baret yok", 4, "hazir"),
    _kkd("yelek", "yelek", "Reflektif yelek yok", 3, "hazir"),
    _kkd("eldiven", "eldiven", "Eldiven yok", 2, "hazir"),
    _kkd("gozluk", "gozluk", "Koruyucu gözlük yok", 3, "hazir"),
    _kkd("bot", "bot", "İş ayakkabısı yok", 2, "hazir"),
    _kkd("onluk", "onluk", "Önlük yok", 2, "hazir", "oznitelik"),
    _kkd("maske", "maske", "Yüz maskesi yok", 2, "hazir", "yokluk"),
    _kkd("siper", "siper", "Yüz siperi yok", 3, "hazir", "yokluk"),
    _kkd("kulaklik", "kulaklik", "Kulak koruyucu yok", 2, "sinirli", "oznitelik"),
    {**_kkd("kemer", "kemer", "Emniyet kemeri yok", 5, "sinirli", "oznitelik"), "bolge_turu": "yuksek", "ciddi": True,
     "aciklama": "Yüksekte çalışma alanı olarak çizilen bölgede tam vücut emniyet kemeri takmayan kişi. Kişi öznitelik modeliyle çalışır "
                 "(kişi görüntüde en az 110 piksel boyundaysa). Eğitimde kullanılmayan gerçek fotoğraflarda kemerli 354 kişiden 348'ini buldu; "
                 "kemersiz 574 kişiden 10'unu kemerli saydı (o kişiler için uyarı çıkmaz). Örnek pres atölyesi klibinde tezgâha eğilmiş, "
                 "şeritli yelekli iki operatörden birini hâlâ kemerli sanıyor."},
    _kkd("tulum", "tulum", "Koruyucu giysi yok", 2, "sinirli", "oznitelik"),
    _kkd("kolluk", "kolluk", "Kolluk yok", 2, "sinirli", "oznitelik"),
    # (solunum koruyucu, maske kuralıyla aynı mekanizma; ayrı sınıf eklendiğinde açılır)


    # ---- alan kontrolü (8)
    {"kod": "carpisma_bolgesi", "ad": "Çarpışma riski bölgesinde kişi ve araç", "kategori": "alan", "mekanizma": "birlikte",
     "bolge_turu": "carpisma", "onem": 5, "ciddi": True, "sure_sn": 0.5, "durum": "hazir",
     "aciklama": "Yolların kesiştiği bölgede aynı anda hem yaya hem araç var."},
    {"kod": "makine_yetkisiz", "ad": "Makine bölgesinde yetkisiz kişi", "kategori": "alan", "mekanizma": "bolge", "ozne": "kisi",
     "bolge_turu": "makine", "yetkili_renk": True, "onem": 4, "ciddi": True, "sure_sn": 1.5, "durum": "hazir",
     "aciklama": "Makine bölgesine, yetkili kıyafet rengini taşımayan biri girdi (kimlik tanıma yok; ayrım kıyafet rengiyle)."},
    {"kod": "en_cok_kisi", "ad": "Alanda fazla kişi", "kategori": "alan", "mekanizma": "sayi", "bolge_turu": "sayim", "en_cok": 4,
     "onem": 3, "ciddi": False, "sure_sn": 3.0, "durum": "hazir", "aciklama": "Alandaki kişi sayısı güvenli sınırın üstünde."},
    {"kod": "en_az_kisi", "ad": "Alanda eksik kişi", "kategori": "alan", "mekanizma": "sayi", "bolge_turu": "sayim", "en_az": 2,
     "onem": 3, "ciddi": False, "sure_sn": 10.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "İş için gereken en az kişi sayısının altına düşüldü (ör. tek başına çalışılmaması gereken iş)."},
    {"kod": "yasak_nesne", "ad": "Yasak nesne", "kategori": "alan", "mekanizma": "nesne", "bolge_turu": "yasak",
     "siniflar": ["telefon", "merdiven"], "onem": 3, "ciddi": False, "sure_sn": 2.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Yasak bölgede bulunmaması gereken bir nesne görüldü (cep telefonu, merdiven). Ek modelle çalışır."},
    {"kod": "yasak_bolge", "ad": "Yasak bölgeye giriş", "kategori": "alan", "mekanizma": "bolge", "ozne": "kisi",
     "bolge_turu": "yasak", "onem": 5, "ciddi": True, "sure_sn": 1.0, "durum": "hazir",
     "aciklama": "Çalışan ekipmanın çevresindeki tehlikeli bölgeye biri girdi."},
    {"kod": "saatli_bolge", "ad": "İzinsiz saatte giriş", "kategori": "alan", "mekanizma": "bolge", "ozne": "kisi",
     "bolge_turu": "yasak", "saatler": ["22:00-06:00"], "onem": 3, "ciddi": False, "sure_sn": 2.0, "durum": "hazir",
     "varsayilan_kapali": True, "aciklama": "Girişin izinli olmadığı saatlerde alanda biri var."},
    {"kod": "bos_kalmamali", "ad": "Gözetimsiz kalan alan", "kategori": "alan", "mekanizma": "sayi", "bolge_turu": "sayim", "en_az": 1,
     "onem": 3, "ciddi": False, "sure_sn": 20.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Başında biri bulunması gereken alan boş kaldı."},

    # ---- davranış (8)
    {"kod": "tirmanma", "ad": "Tırmanma", "kategori": "davranis", "mekanizma": "bolge", "ozne": "kisi", "bolge_turu": "tirmanma",
     "onem": 4, "ciddi": True, "sure_sn": 1.0, "durum": "hazir",
     "aciklama": "Rafa, makineye ya da yapıya tırmanan kişi (tırmanılmaması gereken yüzey bölge olarak çizilir)."},
    {"kod": "kalabalik", "ad": "Dar alanda kalabalık", "kategori": "davranis", "mekanizma": "sayi", "bolge_turu": "sayim", "en_cok": 6,
     "onem": 2, "ciddi": False, "sure_sn": 5.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Dar bir alanda aynı anda çok kişi toplandı."},
    {"kod": "elektrik_bolgesi", "ad": "Elektrik tehlikesi bölgesine giriş", "kategori": "davranis", "mekanizma": "bolge", "ozne": "kisi",
     "bolge_turu": "elektrik", "onem": 5, "ciddi": True, "sure_sn": 1.0, "durum": "hazir",
     "aciklama": "Gerilim altındaki ekipmana temas edilebilecek bölgeye giriş."},
    {"kod": "uc_nokta", "ad": "Merdivende üç nokta teması yok", "kategori": "davranis", "mekanizma": "uc_nokta",
     "onem": 3, "ciddi": False, "sure_sn": 2.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Merdivendeki kişinin el ve ayaklarından üçten azı merdivende (iskeletten). Merdiven, çizilen merdiven bölgesi ya da ek modelin "
                 "bulduğu merdivendir. Temasın kendisi görülemez: el ya da ayağın merdivenin üstünde olması temas sayılır; koli gibi bir yük tutan el sayılmaz."},
    {"kod": "kosma", "ad": "Koşma", "kategori": "davranis", "mekanizma": "hiz", "esik": 1.4, "onem": 2, "ciddi": False, "sure_sn": 0.8,
     "durum": "hazir", "aciklama": "Kayma ya da çarpışma riski olan yerde koşan kişi. Hız, kişinin boyuna oranla ölçülür."},
    {"kod": "yaya_yolu_disi", "ad": "Yaya yolunun dışında yürüme", "kategori": "davranis", "mekanizma": "yol_disi", "ozne": "kisi",
     "bolge_turu": "yaya_yolu", "onem": 3, "ciddi": False, "sure_sn": 2.0, "durum": "hazir",
     "aciklama": "Çizili yaya yolunun dışında yürüyen kişi."},
    {"kod": "kisi_yakinligi", "ad": "Kişiler birbirine çok yakın", "kategori": "davranis", "mekanizma": "yakinlik", "a": "kisi", "b": "kisi",
     "esik": 0.6, "onem": 2, "ciddi": False, "sure_sn": 3.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Kişiler birbirine ya da bir tehlikeye tehlikeli ölçüde yakın."},
    {"kod": "yuksekte_calisma", "ad": "Yüksekte çalışma", "kategori": "davranis", "mekanizma": "bolge", "ozne": "kisi",
     "bolge_turu": "yuksek", "onem": 5, "ciddi": True, "sure_sn": 2.0, "durum": "hazir",
     "aciklama": "Yüksekte çalışma alanı olarak çizilen bölgede kişi var (kemer takıp takmadığına bakılmaz)."},

    {"kod": "dusme", "ad": "Düşen kişi", "kategori": "davranis", "mekanizma": "durus", "durus": "yatma", "onem": 5, "ciddi": True,
     "sure_sn": 2.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Yerde yatan kişi: gövdesi yataya yakın ve kutusu enine (iskeletten). Kameraya doğru ya da kameradan uzağa uzanmış yatan kişi kaçabilir."},

    # ---- araç (5)
    {"kod": "arac_yol_disi", "ad": "Araç kendi yolunun dışında", "kategori": "arac", "mekanizma": "yol_disi", "ozne": "arac",
     "bolge_turu": "arac_yolu", "onem": 3, "ciddi": False, "sure_sn": 2.0, "durum": "hazir",
     "aciklama": "Araç, kendisine ayrılan yolun dışında."},
    {"kod": "arac_yasak_bolge", "ad": "Araç giremez bölgede araç", "kategori": "arac", "mekanizma": "bolge", "ozne": "arac",
     "bolge_turu": "arac_yasak", "onem": 4, "ciddi": True, "sure_sn": 1.0, "durum": "hazir",
     "aciklama": "Araçların girmemesi gereken alana araç girdi."},
    {"kod": "arac_yaya", "ad": "Araç–yaya yakınlığı", "kategori": "arac", "mekanizma": "yakinlik", "a": "arac", "b": "kisi", "esik": 1.0,
     "onem": 5, "ciddi": True, "sure_sn": 0.5, "durum": "hazir",
     "aciklama": "Araç, yaya bir çalışana çarpma riski doğuracak kadar yakın (eşik: kişi boyunun katı)."},
    {"kod": "arac_arac", "ad": "Araç–araç yakınlığı", "kategori": "arac", "mekanizma": "yakinlik", "a": "arac", "b": "arac", "esik": 1.0,
     "onem": 3, "ciddi": False, "sure_sn": 1.0, "durum": "sinirli",
     "aciklama": "İki araç birbirine güvensiz ölçüde yaklaştı (eşik: araçların görüntüdeki aralığı, araç yüksekliğinin katı). "
                 "Sınamada burun buruna ve kesişimde yaklaşan araçlar yakalandı; yan yana bitişik duran iki forklift tek araç sayıldığı için yakalanmadı."},
    {"kod": "ters_yon", "ad": "Ters yönde sürüş", "kategori": "arac", "mekanizma": "ters_yon", "ozne": "arac", "bolge_turu": "arac_yolu",
     "onem": 4, "ciddi": True, "sure_sn": 1.0, "durum": "hazir", "aciklama": "Araç, yolun belirlenen yönüne karşı gidiyor."},

    # ---- düzen (3)
    {"kod": "arac_yolu_engel", "ad": "Araç yolunda engel", "kategori": "duzen", "mekanizma": "nesne", "bolge_turu": "arac_yolu",
     "siniflar": ["palet", "koli", "varil"], "onem": 3, "ciddi": False, "sure_sn": 20.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Araç yolunda bekleyen nesne (palet, koli, varil). Ek modelle çalışır."},
    {"kod": "gecit_engel", "ad": "Geçitte engel", "kategori": "duzen", "mekanizma": "nesne", "bolge_turu": "gecit",
     "siniflar": ["palet", "koli", "varil"], "onem": 3, "ciddi": False, "sure_sn": 20.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Açık tutulacak geçitte bekleyen nesne (palet, koli, varil). Ek modelle çalışır."},
    {"kod": "sahipsiz_nesne", "ad": "Bırakılmış nesne", "kategori": "duzen", "mekanizma": "nesne", "siniflar": ["palet", "koli"],
     "sahipsiz": True, "onem": 2, "ciddi": False, "sure_sn": 30.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Yanında kimse yokken yerinde duran nesne (palet, koli, varil). Ek modelle çalışır."},

    # ---- ergonomi (2)
    {"kod": "reba", "ad": "Zorlayıcı duruş (REBA)", "kategori": "ergonomi", "mekanizma": "ergonomi", "esik": 7, "onem": 3, "ciddi": False,
     "sure_sn": 4.0, "durum": "hazir", "varsayilan_kapali": True,
     "aciklama": "Kişinin REBA duruş puanı eşiğin üstünde kaldı. Puan görüntüdeki eklem açılarından yaklaşık hesaplanır; yük, kavrama ve "
                 "tekrar puanları eklenmediği için tam REBA puanından 1-3 düşük çıkar (tam puanda 8-10 yüksek risk), bu yüzden eşik 7'dir."},
    {"kod": "rula", "ad": "Üst vücut ergonomi riski (RULA)", "kategori": "ergonomi", "mekanizma": "yok", "onem": 2, "ciddi": False,
     "durum": "yok", "aciklama": "Uzanma, montaj ve ayıklama işlerinde üst vücut duruş puanı. Henüz yazılmadı."},
]

KURAL = {k["kod"]: k for k in KURALLAR}

# Panel, her kural için yakalanan durum ve yanlış alarm sayılarını gösterir.
KURAL_SINAMA_OZETI = {
    "baret": {"yakalanan": 3, "beklenen": 3, "yanlis": 0, "denetim": 8},
    "yelek": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 7},
    "ters_yon": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 2},
    "arac_yaya": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 2},
    "yaya_yolu_disi": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 3},
    "arac_yol_disi": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "yasak_bolge": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "makine_yetkisiz": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 1},
    "tirmanma": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "kosma": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 3},
    "kisi_yakinligi": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "gecit_engel": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "en_cok_kisi": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "kalabalik": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "yasak_nesne": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 2},
    "arac_yasak_bolge": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "carpisma_bolgesi": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "arac_arac": {"yakalanan": 2, "beklenen": 3, "yanlis": 0, "denetim": 2},
    "eldiven": {"yakalanan": 4, "beklenen": 4, "yanlis": 0, "denetim": 3},
    "bot": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 3},
    "elektrik_bolgesi": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "yuksekte_calisma": {"yakalanan": 3, "beklenen": 3, "yanlis": 0, "denetim": 1},
    "saatli_bolge": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "en_az_kisi": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "bos_kalmamali": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "sahipsiz_nesne": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "arac_yolu_engel": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "dusme": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 4},
    "reba": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 4},
    "uc_nokta": {"yakalanan": 1, "beklenen": 1, "yanlis": 0, "denetim": 1},
    "gozluk": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 1},
    "siper": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 1},
    "onluk": {"yakalanan": 5, "beklenen": 5, "yanlis": 0, "denetim": 3},
    "kulaklik": {"yakalanan": 5, "beklenen": 5, "yanlis": 0, "denetim": 4},
    "tulum": {"yakalanan": 4, "beklenen": 4, "yanlis": 0, "denetim": 2},
    "kolluk": {"yakalanan": 2, "beklenen": 2, "yanlis": 0, "denetim": 2},
    "maske": {"yakalanan": 3, "beklenen": 3, "yanlis": 0, "denetim": 3},
    "kemer": {"yakalanan": 3, "beklenen": 3, "yanlis": 0, "denetim": 3},
}
for _k in KURALLAR:
    if _k["kod"] in KURAL_SINAMA_OZETI:
        _k["sinama"] = KURAL_SINAMA_OZETI[_k["kod"]]

# Hangi model hangi sınıfları verir. Ek model yalnızca onu gerektiren bir kural açıksa yüklenir.
MODEL_PLANI = [
    {"dosya": "isg-nesne-1280.onnx", "guven": 0.25, "siniflar": ["kisi", "baret", "baret_yok", "yelek", "yelek_yok", "eldiven", "eldiven_yok",
                                                                  "gozluk", "gozluk_yok", "bot", "bot_yok", "forklift"]},
    {"dosya": "isg-ek-1280.onnx", "guven": 0.3, "sinif_guven": {"merdiven": 0.6},  # raf dikmeleri 0.3-0.5 güvenle "merdiven" çıkabiliyor
     "siniflar": ["siper", "kulaklik", "maske", "palet", "koli", "varil", "merdiven", "telefon"],
     "kurallar": ["siper", "kulaklik", "maske", "yasak_nesne", "arac_yolu_engel", "gecit_engel", "sahipsiz_nesne", "uc_nokta"],
     "istege_bagli": ["uc_nokta"]},  # üç nokta kuralı çizilen merdiven bölgesiyle ek model olmadan da çalışır
    # kişi öznitelik modeli: kişi kırpıntısından önlük, tulum, kolluk, emniyet kemeri, kulaklık
    {"dosya": "isg-oznitelik.onnx", "tur": "oznitelik", "kurallar": ["onluk", "tulum", "kolluk", "kemer", "kulaklik"]},
]


# İskelet modeli (İSG İskelet bloğu): eklem noktası olmadan değerlendirilemeyen kurallar
ISKELET_MODELI = {"dosya": "isg-iskelet.onnx",
                  "kurallar": [k["kod"] for k in KURALLAR if k["mekanizma"] in ("durus", "ergonomi", "uc_nokta") or k.get("kanit") == "yokluk"]}

MODEL_KLASORU = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modeller")
MODEL_DOSYALARI = [m["dosya"] for m in MODEL_PLANI] + [ISKELET_MODELI["dosya"]]


def model_kilidi(veri: str | None, klasor: str | None = None) -> dict:
    """Model paketinin kullanılabilirlik durumunu döndürür."""
    return kasa.durum(klasor or MODEL_KLASORU, MODEL_DOSYALARI, veri)


def modelleri_etkinlestir(veri: str, kod: str, klasor: str | None = None) -> None:
    """Etkinleştirme koduyla model anahtarını bu bilgisayara kaydeder (kasa.etkinlestir)."""
    kasa.etkinlestir(klasor or MODEL_KLASORU, veri, kod, MODEL_DOSYALARI)
_EKSIK = {"t": 0.0, "klasor": None, "v": {}}


def eksik_modeller(klasor: str | None = None) -> dict[str, str]:
    """Model dosyası yerinde olmadığı için çalışamayacak kurallar: {kural kodu: eksik dosyanın adı}.

    Ana model yoksa hiçbir kural çalışmaz. Öznitelik modeliyle çalışan kurallar (kanit: oznitelik) yalnızca o modele,
    ötekiler kendilerini listeleyen tespit modeline, eklem noktası gerektirenler ayrıca iskelet modeline bağlıdır.
    Sonuç beş saniye saklanır (her karede diske bakılmasın)."""
    klasor = klasor or MODEL_KLASORU
    simdi = time.monotonic()
    if _EKSIK["klasor"] == klasor and simdi - _EKSIK["t"] < 5.0:
        return _EKSIK["v"]
    out: dict[str, str] = {}
    for m in MODEL_PLANI + [ISKELET_MODELI]:
        if kasa.model_var(os.path.join(klasor, m["dosya"])):
            continue
        oznitelik = m.get("tur") == "oznitelik"
        for kod in m.get("kurallar") or [k["kod"] for k in KURALLAR]:
            k = KURAL.get(kod)
            if k is None or kod in m.get("istege_bagli", ()) or (m.get("kurallar") and (k.get("kanit") == "oznitelik") != oznitelik):
                continue
            out.setdefault(kod, m["dosya"])
    _EKSIK.update(t=simdi, klasor=klasor, v=out)
    return out


def model_plani(ayar: dict) -> list[dict]:
    """Bu kameranın açık kurallarına göre yüklenecek modeller."""
    acik = {k["kod"] for k in coz(ayar)}
    return [m for m in MODEL_PLANI if not m.get("kurallar") or acik & set(m["kurallar"])]


def coz(ayar: dict) -> list[dict]:
    """Kamera ayarındaki "kurallar" listesini katalogla birleştirir.

    Ayarda: "kurallar": [{"kod": "baret"}, {"kod": "en_cok_kisi", "en_cok": 3, "bolge": "Pres hattı"}, ...]
    Liste yoksa: çizilmiş bölge türlerine uyan, varsayılan olarak açık bütün kurallar.
    Model dosyası yerinde olmayan kurallar (eksik_modeller) açık olsa da listeye girmez; panel bunları "model dosyası yok" diye gösterir.
    """
    turler = {b.get("tur") for b in ayar.get("bolgeler") or []}
    istenen = ayar.get("kurallar")
    eksik = eksik_modeller()
    out = []
    if istenen is None:
        for k in KURALLAR:
            if k["durum"] in ("yok", "zayif") or k.get("varsayilan_kapali") or k["kod"] in eksik:
                continue
            if k.get("bolge_turu") and k["bolge_turu"] not in turler:
                continue
            if k["mekanizma"] == "kkd" and not k.get("bolge_turu") and "kkd" not in turler and not ayar.get("kkd_her_yerde"):
                continue
            out.append(dict(k))
        return out
    for i in istenen:
        taban = KURAL.get(i.get("kod", ""))
        if taban is None or taban["durum"] == "yok" or i.get("kapali") or taban["kod"] in eksik:
            continue
        out.append({**taban, **{a: v for a, v in i.items() if a != "kapali"}})
    return out


def olasilik(sure_sn: float, tekrar: int = 1) -> int:
    """1-5: olay ne kadar sürdü ve aynı kural bu kayıtta kaçıncı kez tetiklendi."""
    p = 1 + (sure_sn >= 3) + (sure_sn >= 10) + (sure_sn >= 30)
    p += (tekrar >= 3) + (tekrar >= 8)
    return int(min(5, p))


def risk(onem: int, olas: int) -> int:
    """Risk puanı = önem × olasılık (1-25)."""
    return int(onem) * int(olas)


def duzey(puan: int) -> str:
    return "kritik" if puan >= 15 else "yuksek" if puan >= 9 else "orta" if puan >= 4 else "dusuk"
