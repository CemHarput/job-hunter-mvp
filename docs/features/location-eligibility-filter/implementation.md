# Implementation report: location-eligibility-filter

Sonuç: COMPLETE (koordinatör testleri ve dry-run'ı bağımsız olarak yeniden çalıştırdı: `python -m pytest -q` → 151 passed; `reevaluate --dry-run` DB'yi değiştirmedi, sha256 aynı).

## Değişen dosyalar
Yeni: `jobhunter/locations.py` (tablo yükleme/doğrulama, `BUILTIN_LOCATIONS`, `classify_location`, `LOC_RESTRICTED_PREFIX`, `RANK`), `config/locations.yaml`, `tests/test_locations.py` (81 test).
Değişen: `jobhunter/evaluate.py` (`evaluate(job, profile, locations=None)`, konum `restricted` kontrolü `NEGATIVE`'den sonra, `_positive()`, yeni skor bonusları, `loc` anahtarı), `jobhunter/db.py` (`all_jobs`, `update_heuristics`; şema ve `upsert` aynı), `jobhunter/cli.py` (`load_table`, `scan --locations`, gruplu "Elenen ilanlar", sınıflandırılamayan konum footer'ı, `-rejected.md` arşivi, `list --rejected`/`--limit 0`, `reevaluate`, `locations` alt komutları), `README.md`, `docs/ARCHITECTURE.md`.

## Kabul kriterleri
AC1–AC11 karşılandı; kanıt `tests/test_locations.py` (plan bölüm 4'teki her konum metni parametrik, residue/Narnia, geçersiz tablo, kanıt metinleri, öncelik, Türkiye vakaları, footer ve `locations --unclassified`, 30+1 ilanlı sıkıştırma ve `-rejected.md`, `list --rejected`, `reevaluate` yalnızca 4 alanı değiştirir ve dry-run DB'ye yazmaz, skor sırası) ve mevcut 70 test değişmeden geçiyor. `locations` alt komutunun bozuk tablo hatası kodda var ama testi yok.

## Kontroller
- `python -m pytest -q`: 151 passed, exit 0 (70 eski + 81 yeni).
- `python -m jobhunter reevaluate --dry-run` (gerçek DB, değiştirilmedi; sha256 önce/sonra aynı): değişen 649 / 4563; `unknown -> ineligible: 321`; `global_unconfirmed -> ineligible: 235`; `unknown -> global_unconfirmed: 7`; "daha önce raporlanmış olup artık ineligible: 556".
- `python -m jobhunter locations --unclassified`: yalnızca 4 yazım kaldı (Florianópolis/Santa Catarina, Warsaw/Masovian Voivodeship; her biri 1).
- Çalıştırılmadı: gerçek `scan` (ağ), e-posta, linter yok.

## Sapmalar ve notlar
- `config/locations.yaml` plandaki başlangıç listesine ek olarak gerçek DB'de görülen yazımlar için veri girdileri içerir (Washington, Palo Alto, Dallas, Cardiff, Cork, Paris, Stockholm, Vienna, Herzliya, São Paulo, Sydney, Vilnius, Ontario, Beijing, Taipei, China, Taiwan, APJ→APAC, `Office` ignore vb.).
- Eşit sıralı alternatiflerde sıra: `tr_unknown` > `unclassified` > `remote_bare` > `empty` (planda belirtilmemişti).
- İmplementer erken bir düzenlemede `evaluate.py`'de `\b` karakterlerinin bozulduğunu fark edip onardı; koordinatör kontrol karakteri taramasıyla tüm kaynak dosyalarda temiz olduğunu doğruladı.
- **Koordinatör düzeltmesi:** Windows konsolu (cp1252) `reevaluate` çıktısındaki Türkçe karakterlerde (ğ, ş, ı) `UnicodeEncodeError` ile çöküyordu (implementer'ın ortamında görülmemişti). `cli.main()` başında `stdout/stderr` UTF-8'e yeniden yapılandırıldı (`reconfigure(encoding='utf-8', errors='replace')`); düzeltme sonrası komutlar çalışıyor. Bu satır için ayrı test yok.
- "Remote in Germany" gibi yazımlar residue (`in`) bıraktığı için `unknown` kalır (muhafazakâr).
- Gerçek DB'ye uygulamak için kullanıcının `python -m jobhunter reevaluate` çalıştırması gerekir (veya bir sonraki `scan`).

---
# Review düzeltmeleri (/feature-fast --feature location-eligibility-filter)

Koordinatör (ana oturum) uyguladı; önceki review.md tarihsel kanıttır, yeni `/feature-review` gerekir.

## Kullanıcı kararı (P2-1'in yeniden yorumu)
Review, tablo yokken Hybrid/On-site ilanların elenmesini gerileme saymıştı. Kullanıcı bunun istenen davranış olduğunu söyledi: Hybrid/On-site/In-office elemesi tablodan bağımsız, kod düzeyinde kalır. Bu nedenle **davranış değişmedi**; plan (AC2/AC4) "tablosuz hiçbir eleme yok" derken yanlıştı, bu bir plan sapmasıdır. Düzeltilen: yanıltıcı mesaj ve dokümanlar ve eksik test.

## Düzeltmeler
- **P2-1:** stderr uyarısı "ülke/şehir elemesi kapalı (Hybrid/On-site elemesi yine çalışır)"; `locations.py` yorumu ve `docs/ARCHITECTURE.md` güncellendi; yeni testler `test_onsite_hybrid_eliminated_even_without_table` (None ve BUILTIN ile 5 yazım) ve `test_missing_table_scan_eliminates_hybrid_but_not_countries` (eksik tabloyla `scan`: Hybrid elenir, "United States" elenmez).
- **P2-2:** `list --rejected` `effective_status(r).lower()` kullanır; reviewed `INELIGIBLE` işler listelenir (`test_list_rejected_includes_reviewed_ineligible`: `baseline` ve `raporlanmadı` satırları).
- **P3-1:** `reevaluate` "daha önce raporlanmış olup artık ineligible" sayısı `not reviewed_status` koşuluyla (test: `test_reevaluate_counts_only_unreviewed_newly_rejected`).
- **P3-2:** `_SPLIT` içindeki " or " yalnızca küçük harfle böler; "Portland, OR (Remote)" ve "Portland, OR | Seattle, WA" `ineligible` (`test_us_state_code_or_is_not_a_separator`).
- **P3-3:** konum sınıflandırmasında residue artık Türkiye/restricted kararından önce kontrol edilir (bilinmeyen kelime → `unclassified`, footer'da görünür); ayrıca `evaluate._tr_remote` metin eşleşmesinde olumsuzlama ("excluding", "except", "outside", "without", "cannot", "not eligible/open/…", "no hiring/…") varsa remote+Türkiye eşleşmesini `candidate_tr` saymaz. Testler: `test_turkiye_with_unknown_words_is_not_candidate_tr` (3 yazım), `test_turkiye_plain_cases_still_candidate_tr`. Bu, önceki özelliğin metin mantığındaki mevcut davranışı da değiştirir (daha muhafazakâr).

## Değişen dosyalar
`jobhunter/locations.py`, `jobhunter/evaluate.py`, `jobhunter/cli.py`, `tests/test_locations.py`, `docs/ARCHITECTURE.md`.

## Kontroller
`python -m pytest -q`: 164 passed, exit 0 (önce 151). Gerçek DB üzerinde `reevaluate --dry-run` yeniden çalıştırıldı, DB sha256 önce/sonra aynı (sonuç yukarıdaki komut çıktısında).

## Bilerek yapılmayanlar
Review'un belirsiz hipotezleri: `connect()`→`migrate()` eski DB'de dry-run'da yazma yapar (güncel DB'de değişmez); "Remote, US timezone" restricted sayılır (kabul edilmiş karar); ülke kısaltmaları (`AL`, `GA`, `DE`) "United States" etiketiyle gruplanır (durum doğru, etiket yanıltıcı). `test_reevaluate_touches_only_heuristics` içindeki "değişen 2 / 2" beklentisinin profil farkına dayanması olduğu gibi bırakıldı.
