# Plan: location-eligibility-filter

Kodda doğrulanan olgular:
- `evaluate(job, profile)` şu sırayla çalışıyor: excluded > irrelevant > `NEGATIVE_RE` (`loc + desc` üzerinde) > sözleşme süresi > remote/TR regex'i. Yapısal `location` alanı yalnızca metne ekleniyor.
- `db.upsert` her taramada `eligibility_status/evidence/score/technologies_json` alanlarını eziyor; `reviewed_*` ve `reported_*` alanlarına dokunmuyor. `effective_status`'ta reviewed durum kazanıyor, `excluded` kesin.
- `cli.report()` her ineligible iş için "## Elenen ilanlar (ineligible)" altına bir satır yazıyor. `list_jobs` ineligible işleri atlıyor. `run_scan` raporu `reports/archive/scan-<stamp>.md` dosyasına da yazıyor.
- PyYAML zaten bağımlılık. Şema değişikliği yok; plan `user_version`'a dokunmuyor, job-discovery-sources'un v2 migration'ı ile çakışmaz.
- Testler `evaluate(job, PROFILE)` çağırıyor. `env` fixture'ının Namespace'inde `locations` alanı yok, `list_jobs` `Namespace(db, limit)` ile çağrılıyor.

## 1. Davranış ve kabul kriterleri

- **AC1 Ayrıştırma.** Yeni `classify_location(text, table)` konum metnini NFKC ile normalize eder ve alternatiflere böler. Ayırıcılar: `|`, `;`, `/` ve büyük/küçük harf duyarsız tam kelime ` or `. Virgül bölmez ("Remote, Poland", "San Francisco, CA" tek alternatiftir). Parantezler boşluğa çevrilir.
  - Her alternatifte önce tablodaki terimler eşlenir (en uzun terim önce, `(?<!\w)`/`(?!\w)` sınırları). Sonra koddaki sabit listeler ayıklanır: `REMOTE_WORDS` (remote, remote-friendly, home based/home-based, work from home, wfh, distributed), `ONSITE_WORDS` (hybrid, in-office, in office, on-site, onsite, office-based); sonra `IGNORE` sözcükleri (and, the, of, or, only, based, travel, required, friendly, flexible, location(s)) ve tablodaki `ignore:` listesi atılır. Geriye kalan `\w+` kelimeleri "residue" olur.
  - Alternatif sınıfı, ilk uyan kural sırasıyla: (a) Türkiye terimi var: `ankara` veya remote kelimesi varsa → `turkiye`; Ankara dışı Türkiye şehri varsa ve remote yoksa → `restricted` (etiket "Türkiye (Ankara dışı, yerinde/hibrit)"); yalnızca ülke adı → `tr_unknown`. (b) Residue boş değilse → `unclassified` (residue kaydedilir). (c) Yerinde/hibrit var, remote yok → `restricted` (etiket eşlenen ülke/bölge adları, yoksa "On-site/hybrid"). (d) Açık terim → `open`. (e) Bölgesel terim → `regional`. (f) Kısıtlı terim → `restricted` (etiket kanonik adların sıralı listesi). (g) Yalnızca remote → `remote_bare`. (h) Boş → `empty`.
  - İş düzeyindeki sonuç en iyi alternatiftir. Sıra: `turkiye` > `open` > `regional` > `tr_unknown`/`unclassified`/`remote_bare`/`empty` > `restricted`. Bir iş ancak tüm alternatifleri kısıtlıysa `restricted` olur.
- **AC2 Tablo.** Yeni `config/locations.yaml`. Anahtarlar: `version: 1` (isteğe bağlı), `open`, `regional`, `turkiye`, `turkiye_other_cities`, `ignore` (string listeleri), `restricted` (kanonik etiket → takma ad listesi; etiketin kendisi de takma ad), `us_state_codes` (yalnızca `,\s*XX` biçiminde ve alternatif sonunda/ayırıcıdan önce eşlenir, etiket "United States"). Takma ad `^[A-Z.]{2,5}$` ise (US, USA, UK, U.S., EU, EEA) büyük/küçük harf duyarlı, diğerleri `casefold` ile eşlenir. `load_locations(path)`: mapping olmayan kök, bilinmeyen anahtar, string olmayan değer, aynı normalize terimin iki farklı türde/etikette tanımlanması → `ValueError`. Dosya yoksa `BUILTIN_LOCATIONS` (yalnızca `open`/`regional`/`turkiye`, `restricted` boş → eleme yok) ve `scan` stderr'e "Uyarı: <path> yok; konum elemesi kapalı" yazar. Bozuk dosyada `scan`, `reevaluate`, `locations` "Locations error: …" yazıp 1 döner.
- **AC3 Sonuç eşlemesi ve kanıt metni** (hiçbir durumda VERIFIED üretilmez):
  | Sonuç | Durum | Kanıt metni |
  |---|---|---|
  | `restricted` | `ineligible` | `Location restricted [<etiketler>]: "<konum>" (tüm alternatifler Türkiye dışı veya yerinde/hibrit; kaynağı inceleyin)` (önek sabiti `LOC_RESTRICTED_PREFIX = 'Location restricted ['`) |
  | `open` | `global_unconfirmed` | `Konum açık (<terim>): "<konum>"; Türkiye işe alım uygunluğu doğrulanmadı` |
  | `regional` | `global_unconfirmed` | `Bölgesel (<EMEA/Europe/...>), Türkiye doğrulanmadı: "<konum>"` |
  | `turkiye` | `candidate_tr` | `Konum Türkiye + remote/Ankara: "<konum>"; işe alım koşullarını elle doğrulayın` |
  | diğerleri | mevcut metin mantığı | metin mantığının kanıtı; `unclassified` ise sonuna ` | Sınıflandırılamayan konum: "<residue>"` |
  Konum sonucu ile metin sonucundan iyi olan kazanır (`candidate_tr` > `global_unconfirmed` > `unknown`).
- **AC4 Öncelik ve imza.** `evaluate(job, profile, locations=None)`; `None` → `BUILTIN_LOCATIONS` (I/O yok), mevcut çağrılar aynen çalışır. Sıra: excluded > irrelevant > `NEGATIVE_RE` > konum `restricted` > sözleşme süresi > konum/metin pozitif mantığı > düşük öncelik cezası. Dönüş dict'ine `loc` (sınıf adı) eklenir; `upsert` yok sayar.
- **AC5 Türkiye.** "Remote, Turkey", "Remote - Istanbul", "Ankara" → `candidate_tr`. "Istanbul, Türkiye" ve "Hybrid - Istanbul" → `ineligible`. Yalnızca "Türkiye" → `unknown`, ancak metin mantığı remote+Türkiye görürse `candidate_tr`.
- **AC6 Sınıflandırılamayanlar.** Rapor sonuna `## Sınıflandırılamayan konum ifadeleri` bölümü: listelenen adaylardaki residue taşıyan konum metinleri sayılarıyla, en çok 20; altında `python -m jobhunter locations --unclassified` ipucu; yalnızca böyle konum varsa yazılır. Yeni çevrimdışı komut `python -m jobhunter locations [--unclassified] [--limit 50] [--locations PATH]`: excluded/irrelevant olmayan DB işlerinin farklı konum metinlerini `adet | sınıf | etiket | residue` olarak adede göre azalan sırayla yazar.
- **AC7 Rapor sıkıştırma.** `## Elenen ilanlar (ineligible)` başlığı korunur. `reviewed_status` boş olan ve kanıtı `LOC_RESTRICTED_PREFIX` ile başlayan işler gruplanır: `- <etiketler>: N ilan` satırları (adede göre sıralı). Diğer elemeler (metin `NEGATIVE`, sözleşme, reviewed `INELIGIBLE`) bugünkü gibi iş başına tam satır. Elenen iş varsa her taramada `reports/archive/scan-<stamp>-rejected.md` yazılır: tüm elenenler, `#id | company — title | konum | url | kanıt`. `latest.md` bu dosyanın yolunu ve `python -m jobhunter list --rejected` ipucunu verir. Başlık satırı `Elenen: N (konum: X)`. Elenen iş yoksa metinde "Elenen ilanlar" geçmez. `mark_reported` bugünkü gibi aday + elenen tüm işleri işaretler. Hiçbir iş sessizce gizlenmez.
- **AC8 `list --rejected`** etkin durumu ineligible olan işleri (raporlanmış veya raporlanmamış) `id | reported_via veya 'raporlanmadı' | company — title | konum | url | kanıt` biçiminde yazar; `excluded` yazılmaz; `--limit 0` sınırsız. Bayraksız `list` değişmez.
- **AC9 `reevaluate`.** `python -m jobhunter reevaluate [--profile] [--locations] [--dry-run]` ağa çıkmadan tüm DB işlerini yeniden değerlendirir ve tek transaction'da yalnızca `eligibility_status`, `eligibility_evidence`, `score`, `technologies_json` alanlarını günceller; `reviewed_*`, `reported_*`, `last_seen_at`, `review_events` değişmez. Çıktı: "değişen M / N", geçiş sayıları (`unknown -> ineligible: K`), "daha önce raporlanmış olup artık ineligible: R (list --rejected)". `--dry-run` DB'ye yazmaz.
- **AC10 Skor.** `25 + bonus + teknoloji (en çok 35) + kıdem (10)`, 0..100'e kırpılır. Bonus: `candidate_tr` 25; konum `open` 20; metinle `global_unconfirmed` 15; `regional`/`tr_unknown` 10; remote işareti olan `unknown` 5; diğer 0. Teknoloji ve kıdem eşitken sıra open > regional > unknown. Düşük öncelik cezası (-20) korunur.
- **AC11** Talepteki her konum metni parametrik testte beklenen sonucu verir. Mevcut 70 test geçer.

## 2. İlgili dosyalar ve değişiklik

- **Yeni `jobhunter/locations.py`:** `LocationTable` (dataclass: derlenmiş case-insensitive ve case-sensitive regex, terim → (tür, etiket), state kodları), `BUILTIN_LOCATIONS`, `load_locations(path)` (eksik dosyada `(BUILTIN, missing=True)`), `classify_location(text, table) -> LocationResult(outcome, labels, matched, unclassified)`, sabitler `REMOTE_WORDS`, `ONSITE_WORDS`, `IGNORE_WORDS`, `LOC_RESTRICTED_PREFIX`, `RANK`.
- **`jobhunter/evaluate.py`:** imzaya `locations=None`; `NEGATIVE` döngüsünden sonra konum `restricted` kontrolü; mevcut remote/TR bloğu tek bir `_positive(...)` yardımcısında konum sonucuyla birleştirilir (job-discovery-sources'un aggregator `candidate_tr` → `unknown` düşürmesi bu noktaya bağlanabilir); yeni bonus tablosu. `NEGATIVE`, sözleşme, düşük öncelik ve hariç tutma kodu değişmez.
- **`jobhunter/db.py`:** `update_heuristics(conn, job_id, ev)` (yalnızca 4 alan) ve `all_jobs(conn)`. Şema ve `upsert` değişmez.
- **`jobhunter/cli.py`:** `load_table(path)`; `run_scan` `getattr(args, 'locations', None) or ROOT/'config/locations.yaml'` ile tabloyu `evaluate`'e geçirir; `report()` yeni opsiyonel `rejected_path=None, unclassified=None`, gruplu Elenen bölümü ve footer; rejected arşiv dosyası; `list_jobs` `getattr(args, 'rejected', False)` ve `limit 0`; yeni `reevaluate(args)` ve `locations_cmd(args)`; `main()` alt komutları ve `--locations` argümanları.
- **Yeni `config/locations.yaml`** başlangıç listesi: `open` worldwide, global, anywhere, work from anywhere; `regional` EMEA, Europe, MENA, META, Middle East; `turkiye` Turkey, Türkiye, Turkiye, Ankara; `turkiye_other_cities` Istanbul, İstanbul, Izmir, İzmir, Antalya, Bursa, Kocaeli. `restricted` (etiket: takma adlar): United States (US, USA, U.S., U.S.A., United States of America, San Francisco, New York City, New York, NYC, Seattle, Boston, Austin, Chicago, Los Angeles, Denver), United Kingdom (UK, GB, Great Britain, England, Scotland, London, Manchester), Canada (Toronto, Vancouver, Montreal), Ireland (Dublin), Germany (Berlin, Munich), Netherlands (The Netherlands, Holland, Amsterdam), Spain (Madrid, Barcelona), Portugal (Lisbon), Poland (Warsaw, Krakow), Romania (Bucharest), Bulgaria (Sofia), Greece (Athens), Norway (Oslo), Switzerland (Zurich), India (Bangalore, Bengaluru, Gurugram, Gurgaon, Hyderabad, Pune), Ukraine (Kyiv, Kiev); ülkeler France, Italy, Sweden, Denmark, Finland, Austria, Belgium, Czechia, Hungary, Serbia, Croatia, Estonia, Latvia, Lithuania, Israel, Brazil, Mexico, Argentina, Colombia, Australia, Japan, Singapore; bölgeler Americas (Americas, North America, NAMER, LatAm, Latin America, South America), APAC (APAC, Asia Pacific, Asia), EU/EEA (EU, EEA, European Union), DACH, Nordics. `us_state_codes`: 50 eyalet + DC. `ignore`: time zone, timezone, HQ.
- **`README.md`, `docs/ARCHITECTURE.md`:** konum filtresi, tablo düzenleme, `reevaluate`, `locations`, `list --rejected`, rejected arşivi. `CLAUDE.md` değişmez.
- **Yeni `tests/test_locations.py`.**

## 3. Uygulama adımları

1. `jobhunter/locations.py`: yükleme, doğrulama, `BUILTIN`, `classify_location`.
2. `config/locations.yaml` başlangıç listesi.
3. `evaluate.py`: imza, öncelik, `_positive`, skor bonusları.
4. `db.py`: `update_heuristics`, `all_jobs`.
5. `cli.py`: tablo yükleme ve hata kodu, `run_scan` entegrasyonu, `report()` gruplama, footer ve rejected dosyası, `list --rejected`, `reevaluate`, `locations` alt komutu.
6. `tests/test_locations.py`, ardından README ve ARCHITECTURE.
7. `python -m pytest -q`.

## 4. Doğrulama

Test komutu: `python -m pytest -q` (şu an 70 geçiyor). Testler tabloyu `load_locations(ROOT/'config/locations.yaml')` ile yükler; başlangıç listesi de test edilir.

| AC | Kontrol | Beklenen |
|---|---|---|
| AC1/AC11 | Parametrik konum → durum | `ineligible`: United States, "United States (Remote)", "Canada (Remote)", Spain, Greece, Ireland, United Kingdom, Portugal, Poland, Romania, Norway, Switzerland, Canada, "Bangalore, India", Gurugram, "London, UK", "Sofia, Bulgaria", "Berlin, Germany", Dublin, Toronto, Kyiv, "The Netherlands", Americas, "NAMER and LatAm", "Remote, United Kingdom", "Remote, Canada; Remote, United States", "Remote, Canada; Remote, Poland; Remote, United Kingdom", "San Francisco, CA \| New York City, NY", "San Francisco, CA \| New York City, NY \| Seattle, WA", Hybrid, In-Office, "Spain or Portugal", "Remote, EU". `global_unconfirmed` (açık): "Home based - Worldwide", "Remote, Global", "Remote, Poland \| Worldwide". `global_unconfirmed` + "Bölgesel": "Home based - EMEA", Europe, "Home Based - Americas; Home based - EMEA", "EMEA (Remote) \| United States". `unknown`: Remote, "", "Remote-Friendly (Travel-Required) \| San Francisco, CA", "Berlin \| Remote", "Remote / Hybrid" |
| AC1 | Residue: "Remote, Narnia", "Hybrid, Gotham" | `unknown`, residue `narnia`/`gotham`; reddedilmez |
| AC2 | Eksik dosya; bilinmeyen anahtar; tekrarlı terim; string olmayan değer; büyük harf kuralı ("us" küçük harf eşlenmez) | BUILTIN + uyarı ve eleme yok; `ValueError`; `scan` rc=1 |
| AC3 | Kanıt metinleri | `Location restricted [United States]: "United States (Remote)"…`; "Bölgesel (EMEA), Türkiye doğrulanmadı"; hiçbir durum `VERIFIED_TR` değil |
| AC4 | "Remote, must be US-based" ve "United States" + "3-month contract"; tablosuz `evaluate(job, profile)` | `NEGATIVE` kanıtı kazanır; konum kanıtı sözleşmeden önce gelir; mevcut testler değişmeden geçer |
| AC5 | Türkiye vakaları | AC5 beklentileri |
| AC6 | `env` taraması: Narnia ilanı; `locations --unclassified` capsys | Footer `"Remote, Narnia" (1)`; komut çıktısında satır; ağ yok |
| AC7 | 30 kısıtlı + 1 `NEGATIVE` ilanlı `env` taraması | `latest.md`'de `- United States: 30 ilan`, kısıtlı URL'ler yok; `NEGATIVE` URL'si Elenen bölümünde; `-rejected.md` 31 URL; ikinci taramada "Elenen ilanlar" yok |
| AC8 | `list --rejected` (raporlanmış ineligible iş) | Satır `reported_via` ile görünür; excluded görünmez |
| AC9 | Elle `reviewed_status`/`reported_at` verilmiş DB'de `reevaluate` ve `--dry-run` | Yalnızca 4 alan değişir; reviewed/reported alanları ve `review_events` sayısı aynı; dry-run DB'yi değiştirmez |
| AC10 | Aynı başlık/teknoloji, farklı konum | skor open > regional > unknown, hepsi 0..100 |

**Riskler:** "Berlin, Remote" "Remote in Germany" gibi okunup elenir (yanlış eleme olabilir; satır arşivde görünür). `EU`/`EEA` kısıtlı sayılır (kullanıcı YAML'da `regional`'a taşıyabilir). Başlıktaki konum ipuçları ("(Remote, US)") ayrıştırılmaz. Gruplanmış satırlar e-posta gövdesinde iş başına görünmez; tam liste yerel arşivde ve DB'de. Kanıt önekine dayalı gruplama metin biçimine bağlı; önek tek sabitte tutulur.

**Varsayımlar (ürün kararları, kullanıcı değiştirebilir):** Europe, EMEA, MENA, META ve Middle East `global_unconfirmed` kalır ("Bölgesel … Türkiye doğrulanmadı"). Ankara yerinde/hibrit ilanı `candidate_tr`; Ankara dışı Türkiye şehrinde yerinde/hibrit `ineligible`. Yalın Hybrid/In-Office `ineligible`. Yeni durum değeri eklenmez.

**job-discovery-sources ile uyum:** Şema ve `user_version` değişmiyor; `evaluate` imzası geriye uyumlu; aggregator `location` metinleri aynı tabloyla sınıflanır; onların `candidate_tr` düşürmesi `_positive`'e bağlanmalı; `normalize_company` için hariç tutma kodu bu planda değişmez.

**Açık sorular:** Yok. Not: `start` öncesinde kullanıcının mevcut değişiklikleri commit etmesi gerekir.

READY
