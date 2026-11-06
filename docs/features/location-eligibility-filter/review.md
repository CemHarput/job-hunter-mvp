# İnceleme: location-eligibility-filter

Verdict: **CHANGES_REQUESTED**

## Kapsam ve baz
Baz HEAD `318eeaa` (mevcut HEAD ile aynı). Tam diff, `plan.md`, `implementation.md`, `locations.py`, `config/locations.yaml`, `evaluate.py`, `db.py`, `tests/test_locations.py` okundu; `cli.py` 1-318. satırlar tam, geri kalan (reevaluate, locations_cmd, main) diff üzerinden. Reviewer testleri çalıştıramadı; "151 passed" ve dry-run sonucu koordinatörün bildirdiği ve bağımsız yeniden çalıştırdığı değerlerdir. Kabul edilmiş ürün kararları (yalın Hybrid/In-Office ineligible, EU/EEA restricted, EMEA/Europe regional) bulgu sayılmadı.

## Bulgular
### P2-1 — Tablo yokken (BUILTIN) ve `evaluate(job, profile)` çağrısında Hybrid/On-site işler yine eleniyor
Yer: `jobhunter/locations.py:187-188` (onsite kuralı tablodan bağımsız), `locations.py:130-134` (`BUILTIN_LOCATIONS`), `evaluate.py:122-125`, `cli.py:162`. `config/locations.yaml` yoksa veya tablo verilmeden `evaluate(job(location='Hybrid'), profile)` çağrılırsa `ONSITE_WORDS` eşleşir, residue boş kalır → `restricted [On-site/hybrid]` → `ineligible` ("Hybrid - EMEA", "On-site", "In office" için de). AC2 "restricted boş, eleme yok" ve AC4 "None → BUILTIN, mevcut çağrılar aynen çalışır" ile, ARCHITECTURE'ın "nothing is eliminated by location" ifadesiyle çelişir; stderr "konum elemesi kapalı" derken eleme olur (yanıltıcı). Önceki davranışa göre gerileme (eski kodda yalın "Hybrid" eleme sebebi değildi). `test_no_table_means_no_elimination` yalnızca 'United States' vakasını deniyor. Öneri: `LocationTable`'a "eleme etkin" bayrağı (örn. `restricted_count > 0`); BUILTIN'de (c) kuralı `restricted` yerine `unclassified`/`empty` dönsün; missing-file + `Hybrid` testi.

### P2-2 — `list --rejected` incelenmiş (reviewed) INELIGIBLE işleri göstermiyor
Yer: `cli.py:283`: `if effective_status(r) != 'ineligible': continue`. `review --status INELIGIBLE` ile işaretlenen işte `effective_status` büyük harfli `'INELIGIBLE'` döner, karşılaştırma duyarlı olduğu için iş atlanır. AC8 "etkin durumu ineligible olan işler"; elle incelenmiş ret kayıtları sessizce düşer. `run_scan` (`cli.py:210`) ve `list_jobs` (`cli.py:302`) aynı karşılaştırmada `.lower()` kullanıyor. Öneri: `effective_status(r).lower() != 'ineligible'`; reviewed INELIGIBLE ve `raporlanmadı` satırları için test.

### P3-1 — `reevaluate` "daha önce raporlanmış olup artık ineligible" sayısını şişiriyor (`cli.py:339` civarı)
Koşul `ev['status'] == 'ineligible' and r['reported_at']`; `VERIFIED_TR`/`UNKNOWN` olarak incelenmiş işler etkin durumu review'dan aldığı için `list --rejected`'da görünmez ama sayıya dahil olur. Öneri: `not r['reviewed_status']` ekle veya etkin durum üzerinden say.

### P3-2 — Büyük harfli eyalet kodu `OR`, `" or "` ayırıcısı sanılıyor (`locations.py:30`)
`_SPLIT` `re.I` ile `\s+or\s+`; "Portland, OR (Remote)" normalize edilince "Portland," ve "Remote" diye bölünür → `remote_bare`/`unknown` (aslında restricted). Yanlış tutma (muhafazakâr yön). Öneri: `or` yalnızca küçük harfle eşlensin veya `,\s*OR\b` bölmeden önce korunsun.

### P3-3 — "Türkiye hariç" ifadesi `candidate_tr` ve Türkiye konum kanıtı üretiyor (`locations.py:178-181`)
Türkiye kuralı residue kontrolünden önce çalışır: "Remote - EMEA (excluding Turkey)" → `turkiye` → `candidate_tr` +25, kanıt "Konum Türkiye + remote/Ankara". Durum açısından gerileme yok (eski metin mantığı aynı sonucu veriyordu), VERIFIED değil, ama kanıt metni "excluding"i göstermeden Türkiye'yi kapsadığını iddia ediyor. Öneri: Türkiye dalında residue/olumsuzlayıcı (`exclud*|except|not`) varsa `unclassified`'a düş veya residue'yu kanıta ekle.

## Belirsiz hipotezler (bulgu değil)
- `reevaluate --dry-run`: `connect()` → `migrate()` eski (user_version 0) bir DB'de yazma yapar; güncel DB'de hash değişmedi.
- "Remote, US timezone": `timezone` ignore listesinde olduğundan restricted sayılır (kabul edilmiş "Remote, <ülke>" kararıyla tutarlı).
- `", AL"`, `", GA"`, `", DE"` gibi ülke kısaltmaları "United States" etiketiyle gruplanır; durum doğru (hepsi Türkiye dışı), etiket yanıltıcı.

## Sorun bulunmayanlar
Sınır/büyük-küçük harf kuralları ("Europe" vs "European Union", `EU`/`US` duyarlılığı, Austin/Boston/Cork/Paris/Asia/CAN yalnızca konum alanında), residue taşıyan alternatifin restricted olmaması, değerlendirme sırası ve geriye uyumlu imza, `update_heuristics` (yalnızca 4 alan, tek transaction, dry-run yazmaz, reviewed_*/reported_*/review_events'e dokunmaz), rapor sıkıştırma (reviewed işler gruplanmaz, `-rejected.md` tümünü içerir, `mark_reported` aday+elenen hepsini işaretler, elenen yoksa bölüm yok), UTF-8 `reconfigure` (hasattr korumalı, pipe/capsys altında güvenli).

## Kabul kriterleri
AC1/AC11 karşılanıyor. AC2: eksik dosyada "eleme yok" koşulu Hybrid/On-site için karşılanmıyor (P2-1). AC3, AC5, AC6, AC7, AC10 karşılanıyor. AC4: tablosuz çağrıda gerileme (P2-1). AC8: reviewed INELIGIBLE eksik (P2-2). AC9: rapor sayısında küçük sapma (P3-1).

## Test kanıtı ve sınırlar
P2-1 ve P2-2'yi yakalayan test yok (`test_list_rejected` reviewed INELIGIBLE ve `raporlanmadı` satırlarını denemiyor; `'report' in out` zayıf alt-dize eşleşmesi). `test_reevaluate_touches_only_heuristics` içindeki "değişen 2 / 2" beklentisi konum değişikliğine değil iki DB profilinin farklı teknoloji listelerinden gelen skor farkına dayanıyor. `locations` komutunun bozuk tablo hatası ve `main()` UTF-8 değişikliği testsiz. Gerçek ATS/SMTP denenmedi.
