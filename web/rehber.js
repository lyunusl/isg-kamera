'use strict';
/* Tanıtım turu: ilk açılışta kendiliğinden başlar, sol şeritteki "Tur" düğmesiyle yeniden açılır.
   Her adım bir ekrana gider, ilgili öğeyi vurgular ve ne işe yaradığını anlatır. Tur açıkken sayfaya tıklanamaz:
   hiçbir ayar değişmez, hiçbir analiz başlamaz. uyg.js'teki S, IS, konum, git ve h kullanılır. */

const REHBER_ANAHTARI = 'isg-rehber-goruldu';
const R = { acik: false, i: 0, hedef: null, kaydirildi: false, zamanlayici: null, oncekiOdak: null };
const REHBER_ARKA = ['#ray', '#sayfa', '#katman'];  // tur açıkken etkisiz kılınan bölümler (tıklama ve odak almaz)

/* Tur, varsa ilk kameranın ayar sayfasını gösterir (bölgeler ve kurallar dolu görünsün); kamera yoksa boş "Kamera ekle" formunu. */
const rehberKuralSatiri = kod => document.querySelector(`#k-kurallar input[data-kural="${kod}"]`)?.closest('.kural');
const rehberKamera = () => 'kamera/' + ((S.durum?.kameralar || [])[0]?.kod || 'yeni');

/* yol: gidilecek ekran (yazılmazsa bulunulan ekran) · hedef: vurgulanacak öğe: seçici ya da öğeyi veren işlev
   (sırayla denenir; hiçbiri yoksa kart ortada durur) */
const REHBER_ADIMLARI = [
  { baslik: 'İSG Kamera\'ya hoş geldiniz',
    metin: 'İSG Kamera, güvenlik kameralarınızın görüntüsünde iş güvenliği tehlikelerini arar: takılmayan baret ve yelek, yasak bölgeye giriş, araç–yaya yakınlığı ve benzerleri. Bu kısa tur ekranları ve bir kameranın nasıl eklendiğini gösterir. Tur sırasında hiçbir ayar değişmez.' },
  { hedef: ['#ray'], baslik: 'Ekranlar arasında geçiş',
    metin: 'Ekranlara soldaki şeritten geçilir. Uyarılar\'ın üstündeki sayı, bakılmayı bekleyen açık uyarıları gösterir.' },
  { yol: '', hedef: ['#sayfa .izgara.dortlu', '#sayfa .bos-durum', '#sayfa .ust'], baslik: 'Kontrol Odası',
    metin: 'Günün özeti: güvenlik puanı, koruyucu donanım (KKD) uyumu, izlenen süre ve ciddi risk olayları. Sağ üstten dönemi değiştirebilirsiniz.' },
  { yol: '', hedef: ['#sayfa .kameralar', '#sayfa .bos-durum', '#sayfa .ust'], baslik: 'Kameralar',
    metin: 'Her karo bir kameranın son analiz edilen karesidir; algılanan kişilerin yüzleri gizlenir. Karoya tıklayınca o kameranın ısı haritası açılır.' },
  { yol: 'uyarilar', hedef: ['#sayfa .uyari', '#sayfa .liste', '#sayfa .ust'], baslik: 'Uyarılar',
    metin: 'Bulunan her olay burada listelenir. Bir uyarıya tıklayınca klibi, risk puanı ve konumu açılır; uyarıyı üstlenebilir, çözüldü ya da yanlış alarm diye işaretleyebilirsiniz.' },
  { yol: 'analitik', hedef: ['#sayfa .sahne', '#sayfa .ust'], baslik: 'Görsel Analitik',
    metin: 'Kamera görüntüsünün üstünde ısı haritası ve güzergâhlar: insanlar ve araçlar en çok nerede bulunuyor, olaylar nerede toplanıyor. Önceki dönemle yan yana karşılaştırılabilir.' },
  { yol: 'aksiyonlar', hedef: ['#sayfa .gorev', '#sayfa .ust'], baslik: 'Aksiyonlar',
    metin: 'Bir uyarıdan, sorumlusu ve son tarihi olan görev açılır. Görev kapandıktan sonra aynı kural yeniden görülürse görev "tekrarladı" diye işaretlenir.' },
  { yol: 'raporlar', hedef: ['#sayfa .kart', '#sayfa .ust'], baslik: 'Raporlar',
    metin: 'Güvenlik puanının eğilimi ve olayların kurala, kameraya, vardiyaya ve bölgeye göre dağılımı. Excel raporu buradan indirilir.' },
  { yol: 'kurallar', hedef: ['#sayfa .kural', '#sayfa .ust'], baslik: 'Kurallar',
    metin: 'Programın tanıdığı bütün kurallar. Sağdaki rozet kuralın sınamadaki durumunu gösterir: "Sınamadan geçti" ya da "Sınırlı". Bu ekran yalnızca listedir; bir kural, kameranın kendi ayarında açılır.' },
  { yol: 'ayarlar', hedef: ['#sayfa .ust h1', '#sayfa .ust'], baslik: 'Ayarlar ve AugeLab Studio',
    metin: 'Görüntü işleme AugeLab Studio\'da çalışır. Burada "bağlı" yazmalıdır; yazmıyorsa Studio\'yu açın ve AI Tools → AI Chat Window → Settings → MCP configuration → Start ile bağlantıyı başlatın.' },
  { yol: 'ayarlar', hedef: ['button[data-is="ornek"]', '#a-kameralar'], baslik: 'Önce örnekle deneyin',
    metin: '"Örnek analizi çalıştır", programla gelen dört örnek klibi Studio\'da analiz eder. Birkaç dakika sürer; olaylar klipleriyle birlikte Uyarılar\'a düşer.' },
  { yol: 'ayarlar', hedef: ['a[href="#/kamera/yeni"]', '#a-kameralar'], baslik: 'Kamera ekleme',
    metin: 'Kendi kameranız ya da video kaydınız için "Kamera ekle"ye basılır. Açılan sayfada üç şey doldurulur: bağlantı, bölgeler ve kurallar. Sıradaki adımlar bu sayfayı, en çok da kuralların nasıl eklendiğini gösteriyor.' },
  { yol: rehberKamera, hedef: ['#k-baglanti'], baslik: '1 · Bağlantı',
    metin: 'Kameraya bir ad ve kısa bir kod verin, bağlantı türünü seçin: IP kamera ya da kayıt cihazı (markaya göre RTSP ya da ONVIF), HTTP akışı ya da video dosyası. Kayıt cihazında her kanal ayrı bir kamera olarak eklenir. Kamera şifresi buraya yazılmaz ve kaydedilmez.' },
  { yol: rehberKamera, hedef: ['#cizim-alani', '#k-bolgeler'], baslik: '2 · Bölgeler',
    metin: 'Görüntüde bölgenin köşelerine sırayla tıklayın, türünü seçin (yasak bölge, yaya yolu, araç yolu, KKD zorunlu alan…) ve "Bölgeyi kapat"a basın. Görüntü yoksa kamerayı kaydedip Ayarlar\'da "Bağlantıyı dene"ye basın: alınan kare burada açılır.' },
  { yol: rehberKamera, hedef: ['#k-kurallar'], baslik: '3 · Kurallar',
    metin: 'Kurallar her kamera için ayrı seçilir. Liste gruplara ayrılmıştır: kişisel koruyucu, alan kontrolü, davranış, araç, düzen ve ergonomi. Sıradaki adımlar bir kuralın nasıl açıldığını ve ayarlandığını gösteriyor.' },
  { yol: rehberKamera, hedef: [() => rehberKuralSatiri('baret'), '#k-kurallar .kural'], baslik: 'Kuralı açma ve kapatma',
    metin: 'Her satır bir kuraldır. Soldaki kutuyu işaretleyince kural bu kamerada çalışır, işareti kaldırınca kapanır. Adının altındaki açıklama kuralın neyi aradığını ve nerede yanılabileceğini yazar.' },
  { yol: rehberKamera, hedef: [() => document.querySelector('#k-kurallar [data-kural-alan]')?.closest('.kural'), '#k-kurallar .kural'], baslik: 'Süre ve eşik',
    metin: 'Açıklamanın altındaki kutular kuralın değerleridir. "süre (sn)": durum bu kadar sürerse olay başlar; kısaltmak yanlış alarmı artırır, uzatmak kısa süren durumları kaçırır. Bazı kurallarda "eşik" (ör. yakınlık) ya da "en çok / en az kişi" de vardır.' },
  { yol: rehberKamera, hedef: [() => rehberKuralSatiri('yasak_bolge'), () => [...document.querySelectorAll('#k-kurallar .kural')].find(e => e.textContent.includes('Bölge:')), '#k-kurallar'], baslik: 'Bölge isteyen kurallar',
    metin: 'Açıklaması "Bölge: …" ile başlayan kurallar yalnızca o türde çizilmiş bir bölgede çalışır. Örneğin "Yasak bölgeye giriş" için önce soldaki görüntüde "Yasak bölge" türünde bir bölge çizin. O türde bölge yoksa kural işaretli olsa da olay üretmez.' },
  { yol: rehberKamera, hedef: [() => document.querySelector('#k-kkd-her-yerde')?.closest('label'), '#k-kurallar'], baslik: 'Koruyucu kuralları nerede geçerli?',
    metin: 'Baret, yelek, eldiven gibi kişisel koruyucu kuralları, "KKD zorunlu alan" türünde çizilen bölgelerde aranır. Bu kutu işaretliyse bölge çizmeye gerek kalmaz: görüntünün tamamında aranır.' },
  { yol: rehberKamera, hedef: [() => document.querySelector('#k-kurallar [data-renk]')?.closest('div'), '#k-kurallar'], baslik: 'Yetkili kıyafet rengi',
    metin: '"Makine bölgesinde yetkisiz kişi" kuralı, bölgeye girenin kıyafet rengine bakar. Yetkili personelin giydiği rengi (ör. fosforlu sarı yelek) burada seçin; o renkte giyinmeyen kişi bölgeye girince olay başlar.' },
  { yol: rehberKamera, hedef: [() => [...document.querySelectorAll('#k-kurallar .kural')].find(e => e.querySelector('.rozet.sari, .rozet.kirmizi')), '#k-kurallar'], baslik: 'Rozetler',
    metin: '"Sınırlı" rozetli bir kural çalışır ama sınamada bazı durumları kaçırmıştır; açmadan önce açıklamasını okuyun. "Model dosyası yok" rozetli kural değerlendirilmez. Rozeti olmayan kurallar sınamadan geçmiştir.' },
  { yol: rehberKamera, hedef: ['[data-is="kamera-kaydet"]'], baslik: 'Kaydet',
    metin: 'Kaydet\'e basınca kamera Ayarlar\'daki listeye eklenir. Aynı sayfa sonradan "Düzenle" ile yeniden açılır.' },
  { yol: 'ayarlar', hedef: ['#a-kameralar tr', '#a-kameralar'], baslik: 'Analizi başlatma',
    metin: '"Bağlantıyı dene" kameradan bir kare alır. Video dosyasında "Videoyu analiz et" kaydı baştan sona işler; canlı kamerada "İzlemeyi başlat" durdurulana kadar izler. Aynı anda bir iş çalışır.' },
  { yol: 'ayarlar', hedef: ['#is-karti'], baslik: 'İşin durumu',
    metin: 'Çalışan işin adımları ve varsa hata iletisi burada yazar. İş bitince olaylar Uyarılar\'da ve Kontrol Odası\'nda görünür.' },
  { yol: '', baslik: 'Hazırsınız',
    metin: 'Başlamak için Ayarlar\'dan örnek analizi çalıştırabilir ya da ilk kameranızı ekleyebilirsiniz. Bu turu sol alttaki "Tur" düğmesiyle istediğiniz zaman yeniden açabilirsiniz.' },
];

function rehberGoruldu() { try { return localStorage.getItem(REHBER_ANAHTARI) === '1'; } catch (x) { return true; } }
function rehberIsaretle() { try { localStorage.setItem(REHBER_ANAHTARI, '1'); } catch (x) { /* depolama kapalı: tur yalnızca bu oturumda görünmüş sayılır */ } }

function rehberAc(i = 0) {
  if (!document.getElementById('rehber')) {
    const d = document.createElement('div');
    d.id = 'rehber';
    d.setAttribute('role', 'dialog');
    d.setAttribute('aria-modal', 'true');
    d.setAttribute('aria-label', 'Tanıtım turu');
    d.innerHTML = '<div class="rehber-isik yok"></div><div class="rehber-kart"></div>';
    document.body.appendChild(d);
  }
  $('#katman').innerHTML = '';
  if (!R.acik) R.oncekiOdak = document.activeElement;
  REHBER_ARKA.forEach(s => { const e = document.querySelector(s); if (e) e.inert = true; });
  R.acik = true;
  clearInterval(R.zamanlayici);
  R.zamanlayici = setInterval(rehberYerlestir, 200);  // sayfa yeniden çizilebilir: hedef her seferinde yeniden bulunur
  rehberGoster(i);
}

function rehberKapat() {
  R.acik = false;
  clearInterval(R.zamanlayici);
  document.getElementById('rehber')?.remove();
  REHBER_ARKA.forEach(s => { const e = document.querySelector(s); if (e) e.inert = false; });
  // odak, turdan önceki yerine döner; o öğe yeniden çizildiyse soldaki Tur düğmesine
  (R.oncekiOdak && document.contains(R.oncekiOdak) ? R.oncekiOdak : document.querySelector('#ray a[data-is="rehber"]'))?.focus({ preventScroll: true });
  R.oncekiOdak = null;
  rehberIsaretle();
}

function rehberGoster(i) {
  R.i = Math.max(0, Math.min(REHBER_ADIMLARI.length - 1, i));
  R.hedef = null;
  R.kaydirildi = false;
  const a = REHBER_ADIMLARI[R.i], son = R.i === REHBER_ADIMLARI.length - 1;
  const yol = typeof a.yol === 'function' ? a.yol() : a.yol;
  if (yol != null && konum().yol !== yol) git(yol);
  $('#rehber .rehber-kart').innerHTML = `<button class="rehber-kapat" data-is="rehber-kapat" title="Turu kapat (Esc)" aria-label="Turu kapat">×</button>
    <h3>${h(a.baslik)}</h3><p>${h(a.metin)}</p>
    <div class="rehber-alt"><span class="rehber-say">${R.i + 1} / ${REHBER_ADIMLARI.length}</span>
      ${R.i ? '<button class="dug kucuk" data-is="rehber-geri">Geri</button>' : '<button class="dug kucuk" data-is="rehber-kapat">Turu geç</button>'}
      <button class="dug kucuk ana" data-is="${son ? 'rehber-kapat' : 'rehber-ileri'}" id="rehber-ileri">${son ? 'Bitir' : 'İleri'}</button></div>`;
  $('#rehber-ileri').focus({ preventScroll: true });
  rehberYerlestir();
}

function rehberHedefi() {
  const a = REHBER_ADIMLARI[R.i];
  const yol = typeof a.yol === 'function' ? a.yol() : a.yol;
  if (yol != null && konum().yol !== yol) return null;  // ekran henüz değişmedi
  for (const s of a.hedef || []) {
    const e = typeof s === 'function' ? s() : document.querySelector(s);
    if (e && e.getClientRects().length) return e;
  }
  return null;
}

function rehberYerlestir() {
  const kok = document.getElementById('rehber');
  if (!R.acik || !kok) return;
  const isik = kok.querySelector('.rehber-isik'), kart = kok.querySelector('.rehber-kart');
  const W = window.innerWidth, H = window.innerHeight, pay = 12;
  const e = rehberHedefi();
  if (e && (e !== R.hedef || !R.kaydirildi)) {  // hedef ilk bulunduğunda görünür yere kaydırılır
    R.hedef = e;
    R.kaydirildi = true;
    const r0 = e.getBoundingClientRect();
    if (r0.top < 70 || r0.bottom > H - 20) e.scrollIntoView({ block: r0.height > H * 0.7 ? 'start' : 'center' });
  }
  let r = null;
  if (e) {
    const b = e.getBoundingClientRect();
    const x1 = Math.max(4, b.left - 6), y1 = Math.max(4, b.top - 6), x2 = Math.min(W - 4, b.right + 6), y2 = Math.min(H - 4, b.bottom + 6);
    if (x2 - x1 > 8 && y2 - y1 > 8) r = { x1, y1, x2, y2 };
  }
  const kw = kart.offsetWidth, kh = kart.offsetHeight;
  let kx, ky;
  if (!r) {  // vurgulanacak öğe yok: kart ortada, bütün sayfa karartılır
    isik.classList.add('yok');
    Object.assign(isik.style, { left: '0px', top: '0px', width: W + 'px', height: H + 'px' });
    kx = (W - kw) / 2;
    ky = (H - kh) / 2;
  } else {
    isik.classList.remove('yok');
    Object.assign(isik.style, { left: r.x1 + 'px', top: r.y1 + 'px', width: r.x2 - r.x1 + 'px', height: r.y2 - r.y1 + 'px' });
    const sinirla = (v, enAz, enCok) => Math.max(enAz, Math.min(enCok, v));
    if (r.y2 + pay + kh <= H - pay) [kx, ky] = [sinirla(r.x1, pay, W - kw - pay), r.y2 + pay];                       // altına
    else if (r.y1 - pay - kh >= pay) [kx, ky] = [sinirla(r.x1, pay, W - kw - pay), r.y1 - pay - kh];                // üstüne
    else if (r.x2 + pay + kw <= W - pay) [kx, ky] = [r.x2 + pay, sinirla(r.y1, pay, H - kh - pay)];                 // sağına
    else if (r.x1 - pay - kw >= pay) [kx, ky] = [r.x1 - pay - kw, sinirla(r.y1, pay, H - kh - pay)];                // soluna
    else [kx, ky] = [W - kw - pay - 8, H - kh - pay - 8];                                                           // sığmıyor: sağ alt köşe, öğenin üstünde
  }
  Object.assign(kart.style, { left: Math.round(kx) + 'px', top: Math.round(ky) + 'px' });
}

IS['rehber'] = () => rehberAc(0);
IS['rehber-ileri'] = () => rehberGoster(R.i + 1);
IS['rehber-geri'] = () => rehberGoster(R.i - 1);
IS['rehber-kapat'] = () => rehberKapat();

document.addEventListener('keydown', ev => {
  if (!R.acik) return;
  if (ev.key === 'Escape') rehberKapat();
  else if (ev.key === 'ArrowRight') { if (R.i < REHBER_ADIMLARI.length - 1) rehberGoster(R.i + 1); }
  else if (ev.key === 'ArrowLeft') { if (R.i > 0) rehberGoster(R.i - 1); }
  else if (ev.key === 'Tab') {  // odak turun dışına çıkmasın
    const d = [...document.querySelectorAll('#rehber button')];
    const sira = d.indexOf(document.activeElement);
    ev.preventDefault();
    d[(sira + (ev.shiftKey ? d.length - 1 : 1)) % d.length]?.focus();
  }
});
window.addEventListener('resize', rehberYerlestir);
window.addEventListener('scroll', rehberYerlestir, true);

/* İlk açılış: program hazır olunca (durum okununca) tur bir kez kendiliğinden başlar. */
(function ilkAcilis(deneme = 0) {
  if (rehberGoruldu()) return;
  if (S.durum && S.katalog) { rehberIsaretle(); return rehberAc(0); }  // gösterildiği an işaretlenir: sayfa yenilenirse yeniden başlamaz
  if (deneme < 100) setTimeout(() => ilkAcilis(deneme + 1), 150);
})();
