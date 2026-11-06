# İnceleme #3: harden-scan-pipeline (review #2 düzeltmeleri sonrası)

Verdict: **CHANGES_REQUESTED**

(Önceki review #1 ve #2 bu dosyayla değiştirildi.)

## Kapsam ve baz
Baz HEAD `67dfe53` (mevcut HEAD ile aynı). Tam diff, `evaluate.py`, `tests/test_scan.py`, `db.py`, `cli.py` (diff) ve feature artifact'ları okundu. Reviewer testleri çalıştıramadı; "68 passed" koordinatörün bildirdiği sonuçtur. Regex davranışı kodu elle izleyerek çıkarıldı.

## Bulgular
### P2 — Türkiye'den sonra gelen olumsuzluk kısıtlamayı hâlâ kurtarıyor (`evaluate.py:31`, `:75-80`)
`TR_LIST` yalnızca eşleşmeden Türkiye'ye kadar olan kısma bakıyor; Türkiye'den sonrasını kontrol etmiyor. "Remote EU only, Turkey excluded" → `EU only` kurtarılır, `tr_remote`+`verified` tutar → `candidate_tr`, skor +25. Aynı sonuç: "Must be based in the US or Canada, Turkey not eligible", "Remote in the EU, Türkiye excluded", "Remote within the UK and Turkey excluded". Test boşluğu: `tests/test_scan.py:221-228` yalnızca Türkiye'den önceki olumsuzlukları kapsıyor. Öneri: Türkiye'den sonraki aynı cümlede `excluded|not eligible|not|except|excluding|ineligible` varsa kurtarmayı iptal et; testler.

### P3 — Tanımsız ülke içeren gerçek listeler eleniyor (`evaluate.py:31`)
"Remote in the UK, Spain, Portugal or Türkiye", "Must be based in the EU, Serbia or Turkey", "Remote in the EU or in Türkiye", "Remote within the EU (incl. Türkiye)" `ineligible` olur (Elenen bölümünde görünür, sessiz kayıp yok). Aynı kökten: `raw = f'{loc} {desc}'` konum+açıklamayı tek boşlukla birleştirir ve `TR_LIST` `\s` ile satır sonunu geçer (konum "US only" + açıklama "Türkiye's leading fintech…" → kurtarılır). Öneri: aynı cümlede büyük harfli ülke benzeri kelimelere ve `in`/`(incl.`/`including`'e izin ver; konum ile açıklama arasına ayraç koy, `TR_LIST`'te `\n` dışla; pozitif testler.

## Review #2 bulgularının doğrulaması
P2 (Türkiye penceresi) büyük ölçüde düzeldi: on-site/hybrid istisnasız; `;`, `.`, "cannot hire in", "not open to", "We also hire in Turkey…" kurtarmıyor; `finditer` döngüsü sonraki eşleşmelere devam ediyor (kısıtlama gözden kaçmıyor). P3 sözleşme kalıpları düzeldi, felaket geri izleme yok, uzun süreli ilanları eleyen somut yanlış pozitif bulunmadı. P3 test boşluğu kısmen kapandı. Taşımada `upper()` ve alıntı normalizasyonu regresyonsuz.

## Gözlemler (bulgu değil)
- "Temporary 3-month relocation/housing support" `temporary` yüzünden 3 aylık sözleşme sayılabilir (nadir).
- `evaluate.py:49` "wider slice first" yorumu gerçek davranışla örtüşmüyor (davranış doğru, yorum yanıltıcı).
- `_review_text` içinde `notes` tek satıra indirilmiyor (kozmetik).

## Kabul kriterleri
AC1–AC8, AC10, AC12–AC14 karşılanıyor. AC9 büyük ölçüde: Türkiye sonrası olumsuzluk (P2) ve tanımsız ülkeli listeler (P3) açık. AC11 karşılanıyor.

## Test kanıtı ve sınırlar
Gerçek ATS/SMTP denenmedi; testler `collect` ve `send_email`'i mock'luyor.
