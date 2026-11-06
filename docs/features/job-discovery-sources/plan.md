# Plan: job-discovery-sources

Doğrulanmış olgular (kod okundu): pipeline `collectors.collect(sources) -> (jobs, errors)` → `evaluate.evaluate(job, profile)` → `db.upsert(conn, job, ev)` (yeni iş ise True) → `cli.run_scan` → `cli.report()`. `tests/test_scan.py` içindeki `env` fixture'ı `cli.collect`'i `lambda sources: (...)` ile, `cli.send_email`'i de mock'luyor; `collect` imzası geriye uyumlu kalmalı. `db.migrate()` eksik kolonları `NEW_COLUMNS` ile ekliyor, tek seferlik dönüşüm için `PRAGMA user_version` kullanıyor (şu an 1). Hariç tutma `evaluate` içinde birebir `casefold()` eşitliğiyle; `is_applied` şirket + (url veya başlık). RSS için yeni bağımlılık gerekmiyor (`xml.etree`). Feed alanları koordinatörün 2026-11-06 canlı yoklamasından; planlayıcının web erişimi yoktu.

## 1. Davranış ve kabul kriterleri

`scan`, `sources.yaml` içindeki yeni `aggregators:` bölümünde açık olan altı public feed'i (Remotive, Remote OK, Arbeitnow, We Work Remotely, Himalayas, HN Who is hiring) ATS panolarıyla birlikte tarar. Her rapor girdisi kaynağı ve kaynak linkini gösterir. Kaynaklar arası tekrarlar tek kez raporlanır. Aggregator ilanlarında görülen Greenhouse/Lever/Ashby panoları aday olarak saklanır; yeni `discover` komutu bunları listeler ve istenirse doğrular. `sources.yaml` dosyasına hiçbir zaman yazılmaz.

- **AC1** Her aggregator için bir parser kayıtlı fixture'dan mevcut iş dict'ini üretir. Ek alanlar: `source` (`remotive|remoteok|arbeitnow|weworkremotely|himalayas|hackernews`), `source_name`, `source_url`, `apply_url` (yalnızca feed'de varsa, yoksa `None`), `aggregator=True`, `links` (pano çıkarımı için URL'ler; DB'ye yazılmaz). Feed'de olmayan tarih, ücret veya başvuru linki üretilmez.
- **AC2** `aggregators:` yapılandırması: bölüm yoksa/`{}` ise hiçbir aggregator çalışmaz (geriye uyum; testler ağa çıkmaz); kayıt varsa ve `enabled: false` değilse açık; bilinmeyen ad veya geçersiz seçenek `Kaynak hataları`'na `config/<ad>: ...` olarak yazılır, tarama sürer; gelen `config/sources.yaml`'da altısı da açık; mevcut `greenhouse/lever/ashby` listeleri değişmeden çalışır.
- **AC3** Feed düzeyinde hata (HTTP 4xx/5xx, timeout, bozuk JSON/XML, boyut sınırı aşımı, DOCTYPE/ENTITY içeren RSS) `<source>: <hata>` olarak raporlanır; diğer feed'ler sürer, retry yok. Kayıt düzeyinde bozuk öğe atlanır; `<source>: N kayıt atlandı (bozuk)` raporlanır.
- **AC4** Her feed'e taramada bir istek (WWR'da kategori başına; HN istisna). User-Agent `JobHunterMVP/0.2 (personal job search; public feeds)`. Gövde stream ile okunur, `max_bytes` (varsayılan 8 MB) aşılırsa feed hatası.
- **AC5** Aggregator başına `max_age_days`: daha eski tarihli öğeler toplanırken düşer, tarihsizler tutulur. Raporun yaş filtresi ayrıca çalışır.
- **AC6** Kaynaklar arası tekrar: farklı `source`'tan aynı `dedupe_key`'e (normalize şirket + normalize başlık) ve uyumlu konuma sahip mevcut satır varsa yeni satır açılmaz; gelen iş `alt_sources_json`'a eklenir (idempotent, URL'ye göre). İlk görülen kazanır; aynı taramada ATS panoları önce işlenir. Aynı `source` içindeki işler birbirinin tekrarı sayılmaz. Konumlar uyumsuzsa (ör. "US" ile "Europe") birleştirme yok. Raporda "Kaynaklar arası tekrar: N" satırı.
- **AC7** Raporda aggregator ilanı için `- Kaynak: <source_name> — <source_url>`. `- Başvuru:` satırı `apply_url` varsa onu, yoksa `<url> (kaynak sayfası; feed başvuru linki vermiyor)`. Alternatifler `- Diğer kaynaklar: <ad> — <url>; ...`. "Elenen ilanlar" satırına `(kaynak: <source_name>)`. ATS ilanlarının görünümü değişmez.
- **AC8** Aggregator konumu ve etiketleri `evaluate()`'e girer: konum `location`, tags/job_type/seniority/employmentType `description` sonuna eklenir. Aggregator işi asla `candidate_tr` veya VERIFIED olmaz: sezgi Türkiye gördüğünde durum `unknown`, kanıt metni "Türkiye ipucu (aggregator metni), işverende doğrula", skor bonusu korunur. "Worldwide" içeren ilan `global_unconfirmed`.
- **AC9** Hariç tutmalar normalize şirket adıyla: casefold, noktalama temizliği, `inc|llc|ltd|gmbh|corp|co|bv|sa|plc` eklerinin atılması. Normalize ad hariç tutulan adla aynıysa veya hariç tutulan ad + boşlukla başlıyorsa hariç ("Deel Inc.", "EPAM Systems"; "Insider One" korunur). Aynı kural `low_priority_companies` için. Başvurulmuş işler aggregator kaynaklı olsa da listelenmez.
- **AC10** `discover.extract_boards(urls)` url/apply_url/açıklama href'lerinden `boards.greenhouse.io/<token>`, `job-boards.greenhouse.io/<token>`, `greenhouse.io/embed/job_board?for=<token>`, `jobs.lever.co/<site>`, `jobs.ashbyhq.com/<board>` (URL-decode) tanır; `.eu` host'ları, `embed`, boş/geçersiz tanımlayıcılar tanınmaz. Scan, hariç tutulmamış işlerde bulunan ve `sources.yaml`'da olmayan panoları `board_candidates` tablosuna yazar. Raporda "Yeni pano adayları: N (python -m jobhunter discover)".
- **AC11** `python -m jobhunter discover [--verify] [--limit 20]`: yapılandırılmamış ve hariç tutulmamış adayları listeler. `--verify` her aday için mevcut public endpoint'e tek GET atar, 200 + beklenen JSON şeklini kontrol eder (greenhouse `{"jobs": [...]}`, lever liste, ashby `{"jobs": [...]}`). Şirket eşleşmesi `MATCH|MISMATCH|UNCONFIRMED`. Yalnızca doğrulananlar için kopyalanabilir YAML stdout'a; `sources.yaml` byte düzeyinde değişmez.
- **AC12** Migration: eski DB açıldığında `source_name, source_url, apply_url, alt_sources_json, dedupe_key` kolonları eklenir; `user_version < 2` bloğu mevcut satırların `dedupe_key`'ini Python'da doldurur ve `user_version = 2` yapar; `board_candidates` tablosu SCHEMA'da `CREATE TABLE IF NOT EXISTS`; ikinci açılışta değişiklik yok.
- **AC13** HN: `whoishiring` kullanıcısının submitted id'lerinden en yenisi taranır (en çok 10 öğe): başlığı `^Ask HN: Who is hiring\? \(\w+ \d{4}\)$` olan ve son 45 gün içinde açılan thread seçilir; "Who wants to be hired?" ve "Freelancer?" seçilmez. En fazla `max_comments` (varsayılan 200) top-level yorum sırayla çekilir; 60 sn feed bütçesi aşılınca durulur. Silinmiş/dead/metinsiz yorumlar, ilk satırında 2'den az `|` olanlar ve rol benzeri segmenti olmayanlar gürültü sayılıp sessizce atlanır; sayıları yalnızca özet satırında.
- **AC14** Ağa çıkmadan çalışan fixture testleri; mevcut 70 test geçmeye devam eder.

## 2. İlgili dosyalar ve değişiklik

- `jobhunter/collectors.py`: `collect(sources, client=None)` imzası ve `(jobs, errors)` dönüşü korunur; ATS döngüsünden sonra `aggregators.collect_aggregators(client, sources.get('aggregators') or {}, jobs, errors)`; UA güncellenir; `except` listesine `xml.etree.ElementTree.ParseError`.
- **Yeni** `jobhunter/aggregators.py`: `get_capped(client, url, max_bytes)` (`client.stream`, sınır aşımında `ValueError`); `AGGREGATORS = {name: (fetch_raw, parse_item, defaults)}`; öğe başına `KeyError/TypeError/ValueError/AttributeError` yakalanıp sayılır; açıklama `links` toplandıktan sonra `clean_html` ile temizlenir. Alan eşlemeleri:
  - Remotive: `url → url/source_url`, `candidate_required_location → location`, `publication_date`; `apply_url=None`.
  - Remote OK: `url`, `apply_url`, `location`, `date|epoch`, `company`, `position`; `id`/`position` içermeyen legal öğe atlanır.
  - Arbeitnow: `url`, `company_name`, `title`, `location`, `remote`, `tags`, `job_types`, `created_at` (doğrulanmalı); `remote_only: true` seçeneği; URL'si olmayan öğe `valid_job` ile düşer.
  - WWR: RSS `item/title` "Company: Title" bölünür (`: ` yoksa atlanır), `link`, `guid`, `pubDate` (`email.utils.parsedate_to_datetime`), `region` (varsa). İlk 2 KB'ta `<!DOCTYPE`/`<!ENTITY` varsa feed reddedilir. Kategori slug'ı `^[a-z0-9-]+$`.
  - Himalayas: `?limit=<limit>` tek istek, cursor takip edilmez; `companyName`, `title`, `locationRestrictions` (boşsa `''`; "worldwide" uydurulmaz), `applicationLink → apply_url`, `guid` https ise `source_url`, `pubDate`.
  - HN: `url=source_url=https://news.ycombinator.com/item?id=<id>`, `apply_url=None`, `time → published_at`, `company` = ilk segment (`(YC ...)` atılır), `title` = `evaluate.ROLE` veya `engineer|developer` içeren ilk segment, `location` = remote/onsite/hybrid/worldwide geçen segmentler.
- **Yeni** `jobhunter/dedupe.py`: `normalize_company`, `normalize_title` (`sr.→senior`, noktalama), `dedupe_key(company, title)`, `locations_compatible(a, b)` (generic token'lar `remote, worldwide, anywhere, global, only` atıldıktan sonra kümelerden biri boşsa veya kesişim boş değilse uyumlu).
- `jobhunter/evaluate.py`: hariç tutma ve düşük öncelik `normalize_company` ile (AC9); aggregator `candidate_tr` düşürmesi (AC8). Döngüsel import yok (HN parser `ROLE`'ü evaluate'ten alır, evaluate dedupe'u import eder).
- `jobhunter/db.py`: SCHEMA ve `NEW_COLUMNS`'a 5 kolon, `idx_jobs_dedupe`; `board_candidates(id, provider, identifier, company, example_url, first_seen_at, last_seen_at, UNIQUE(provider, identifier))`; `migrate()` içine `user_version < 2` bloğu; `upsert` ek kolonları `job.get()` ile yazar, `dedupe_key`'i kendisi hesaplar (dönüş aynı); yeni: `find_duplicate`, `add_alternate`, `record_board_candidate`, `board_candidates`.
- **Yeni** `jobhunter/discover.py`: `extract_boards(urls)`, `configured(sources)`, `verify(client, provider, ident, company) -> (ok, match, note)`.
- `jobhunter/cli.py`: `run_scan` döngüsünde `ev` hesaplanır; satır `(source, source_id)` ile yoksa ve `find_duplicate` eşleşirse `add_alternate` + `dup_count++` + `continue`, aksi halde `upsert`; hariç tutulmayan işlerin `links` alanından pano adayları kaydedilir. `report()` yeni opsiyonel parametreler `dup_count=0`, `new_boards=0`. `_job_lines` ve Elenen satırına AC7. `discover(args, client=None)` alt komutu (`--profile`, `--sources`, `--verify`, `--limit`).
- `config/sources.yaml`: `aggregators:` bölümü (`remotive: {category: software-dev, max_age_days: 30}`, `remoteok`, `arbeitnow: {remote_only: true}`, `weworkremotely: {categories: [remote-back-end-programming-jobs]}`, `himalayas: {limit: 100}`, `hackernews: {max_comments: 200}`; hepsinde `max_age_days: 30`). Mevcut ATS board listesi korunur.
- `README.md`, `docs/ARCHITECTURE.md`: aggregator'lar, atıf koşulları (Remotive: üçüncü taraf sitelere aktarım yok; rapor yalnızca yerel/kendi e-postanız için), `discover`, dedupe sınırları. `CLAUDE.md` değişmez.

## 3. Uygulama adımları

1. Canlı yanıtları alan adlarıyla yeniden doğrula; her feed'den 2-3 öğelik kırpılmış örneği `tests/fixtures/aggregators/{remotive.json, remoteok.json, arbeitnow.json, wwr.rss, himalayas.json, hn_user.json, hn_story.json, hn_comments.json}` olarak kaydet, her birine bir bozuk öğe ekle. Ağ yoksa fixture'ları belgelenen anahtarlardan kur ve `implementation.md`'de belirt.
2. `dedupe.py` ve testleri.
3. `db.py`: SCHEMA, `NEW_COLUMNS`, v2 migration, yeni fonksiyonlar, `upsert` genişletmesi.
4. `evaluate.py`: normalize hariç tutma ve aggregator `candidate_tr` düşürmesi.
5. `aggregators.py` ve `collectors.collect` entegrasyonu.
6. `discover.py`, ardından `cli.run_scan` / `report` / `discover` alt komutu.
7. `config/sources.yaml`, README, ARCHITECTURE.
8. Yeni testler `tests/test_aggregators.py` ve `tests/test_discovery.py` (httpx `MockTransport`, `tmp_path`, mevcut `env` fixture deseni); tam test paketi.

## 4. Doğrulama

Test komutu: `python -m pytest -q` (testlerde gerçek ağ yok; canlı `scan`/`discover` isteğe bağlı manuel kontrol, kanıt sayılmaz).

| AC | Kontrol | Beklenen |
|---|---|---|
| AC1 | Her fixture için parser testi | Alanlar doğru; `apply_url` yalnızca feed'de varsa dolu; Remote OK legal öğesi yok |
| AC2 | `{}` ile collect (MockTransport'a istek yok); `enabled: false`; bilinmeyen ad | 0 istek; feed atlanır; `config/x` hatası |
| AC3 | 500 / bozuk JSON / DOCTYPE RSS / bozuk öğe | Hata satırları; diğer feed işleri gelir; atlanan sayısı raporlanır |
| AC4 | `max_bytes=100` ile büyük gövde; istek sayacı; UA | Boyut hatası; feed başına 1 istek; UA doğru |
| AC5 | 100g, 2g, tarihsiz öğe, `max_age_days: 30` | 100 günlük düşer |
| AC6 | `env`: ATS + Remotive aynı iş; farklı konum; aynı source iki iş | Tek `##` + "Diğer kaynaklar"; uyumsuz konumlar ve aynı source ikisi de listelenir |
| AC7 | Rapor metni | "Kaynak: Remotive — https://remotive.com/..." ve başvuru satırı kuralı |
| AC8 | "Remote, Turkey" aggregator işi; "Worldwide" | `unknown` + ipucu; `global_unconfirmed`; asla `candidate_tr` |
| AC9 | "Deel Inc.", "EPAM Systems", başvurulmuş aggregator işi | `excluded`; listelenmez |
| AC10 | URL tablosu testi; scan sonrası `board_candidates` | Doğru (provider, id); `.eu`/embed yok; hariç tutulan şirket kaydedilmez |
| AC11 | `discover --verify` MockTransport 200 / 404 / geçersiz JSON / mismatch; öncesi-sonrası sources.yaml bytes | YAML yalnızca 200+geçerli için; sources.yaml değişmez |
| AC12 | v1 şemalı eski DB'yi iki kez aç | Kolonlar var, `dedupe_key` dolu, `user_version=2`, idempotent |
| AC13 | HN fixture: wants-to-be-hired thread'i, eski thread, gürültü yorumları | Doğru thread; yalnızca geçerli başlıklı yorumlar; `max_comments` uygulanır |
| AC14 | `python -m pytest -q` | 70 eski test + yenilerin hepsi geçer |

**İmplementer'ın canlı yanıtla yeniden doğrulaması gerekenler:** Arbeitnow `url/location/remote/created_at/tags/job_types`; Himalayas `guid` biçimi, `locationRestrictions` öğe tipi, `pubDate` birimi; WWR `region`/`category` elemanları ve başlık biçimi; Remote OK `date` biçimi; HN `kids` alanı ve başlık metni; Greenhouse jobs yanıtında `company_name` olup olmadığı; `job-boards.greenhouse.io` token'ının `boards-api` ile çalışıp çalışmadığı.

**Riskler:** HN için "feed başına tek istek" kuralına istisna gerekir (Firebase öğe başına istek; en fazla 200 + 60 sn bütçe; Algolia kullanılmayacak). İlk aggregator taraması kalabalık rapor üretebilir (`max_age_days: 30`, rapor filtreleri, `scan --baseline`). Dedupe sezgiseldir (başlık farklıysa tekrar kaçabilir, konum boşsa yanlış birleşebilir; birleşen kaynak "Diğer kaynaklar"da görünür). Önek eşleşmeli hariç tutma ("Mercury Insurance") fazladan eleme yapabilir (bilinçli muhafazakâr). `defusedxml` eklenmez; DOCTYPE/ENTITY reddi ve boyut sınırı korunma sağlar.

**Varsayımlar:** Aggregator işinde `candidate_tr` yerine `unknown` + ipucu; `aggregators` bölümü yoksa hiçbiri çalışmaz (gelen config'de altısı açık); tekrarda yeni satır yok, alternatif URL kaydı; salary raporda "Doğrulanmadı"; pano adayları rapor gövdesine girmez (yalnızca bildirim satırı), ayrıntı `discover`'da.

**Açık sorular:** Yok. Not: önceki feature'ın commit edilmemiş değişiklikleri nedeniyle `start` öncesinde kullanıcının commit yapması gerekir. Ayrıca bu plan konum tabanlı uygunluk filtresini kapsamaz (ilk gerçek tarama konum filtresi eksikliğini gösterdi; ayrı feature olarak ele alınır).

READY
