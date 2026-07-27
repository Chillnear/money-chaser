# Money Chaser

ระบบเทรด AI multi-agent สำหรับ BTC/คริปโต/ทองดิจิทัล (PAXG) บน Hyperliquid — วันละ 1 ไม้

เอกสารหลัก: **`../BUILD-SPEC.md`** (สเปกวิศวกรรม) และ **`../TASKS.md`** (คิวงาน) อยู่ในโฟลเดอร์ Money Chaser ระดับบน

## สถานะปัจจุบัน

### P0 — เสร็จสมบูรณ์ ✅
- [x] 0.1 โครง repo + requirements.txt
- [x] 0.2 settings.py + config.yaml/risk.yaml (validate ด้วย pydantic, fail-closed)
- [x] 0.4 `src/data/hl_market.py` (+ `get_clearinghouse_state`, `get_funding_history`) — verify จริงบน GitHub Actions แล้ว (BTC mid ✅, main wallet balance ✅, 232 ตลาดรวม PAXG ✅)
- [x] 0.3 LiteLLM probe เต็มรูปแบบ — `config/models.yaml` เขียนแล้วจากผลจริง (47 โมเดลใช้งานได้ กระจาย 4 role แรกครบ 4 ค่าย: Alibaba/Anthropic/DeepSeek/Google, judge=Claude Opus 4.5, reflector=GPT-5.5)
- [x] 0.5 Agent wallet ใช้งานได้จริง — ยืนยันผ่าน clearinghouseState query แล้ว

### P1 — data layer / features / screening (เขียนเสร็จ, unit test ผ่านหมด 66/66)
- [x] 1.1 `src/data/macro.py`, `sentiment.py`, `news.py` (+ `cryptopanic.py`, RSS ขยายเป็น 6 แหล่ง: CoinDesk/Cointelegraph/Decrypt/TheBlock/BitcoinMagazine/CryptoSlate) — mock test ผ่านครบ
  - CryptoPanic เป็น optional news aggregator (สมัครฟรีที่ cryptopanic.com/developers/api/keys) รวมข่าวหลายแหล่ง+sentiment โหวตจากชุมชน โดยไม่ต้อง scrape Twitter/Telegram ตรงๆ (ผิด ToS และเปราะบาง) — ถ้าไม่ตั้ง `CRYPTOPANIC_API_KEY` ระบบข้ามไปเฉยๆ
- [x] 1.2 `src/data/features.py` — indicator ทั้งหมดตาม golden test
- [x] 1.3 `src/data/regime.py` — จำแนก 3x3 regime
- [x] 1.4 `src/data/screening.py` — universe pool + composite score + top-3 shortlist
- [x] 1.5 `render_feature_table()` — ตาราง prompt แบบ compact, เช็ค token budget แล้ว
  - ⚠️ **macro.py/sentiment.py/news.py ยังไม่ได้ยิงทดสอบกับ network จริง** (เหตุผลเดียวกับ hl_market.py — sandbox บล็อก) ต้องรัน workflow "Test Setup (manual)" อีกรอบเพื่อ verify (ดูขั้นตอนด้านล่าง เพิ่ม check ใหม่แล้วใน `check_setup.py`)

## วิธีติดตั้ง (บนเครื่องที่มี network ปกติ)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # แล้วกรอกค่าจริง
```

## วิธีรันเทส

```bash
pytest -q                      # unit tests ทั้งหมด (ไม่แตะ network จริง)
python -m src.settings         # smoke test โหลด config
```

## วิธีอัปโหลดขึ้น GitHub ด้วย GitHub Desktop (ไม่ต้องใช้ command line)

1. ติดตั้ง [GitHub Desktop](https://desktop.github.com/) แล้ว sign in ด้วยบัญชี GitHub ของคุณ
2. เมนู **File → Add Local Repository** → เลือกโฟลเดอร์นี้ (โฟลเดอร์ `money-chaser` ที่มีไฟล์นี้อยู่) — โฟลเดอร์นี้มี git history อยู่แล้ว (ผมสร้างไว้ให้แล้ว) GitHub Desktop จะเจอเองอัตโนมัติ
3. กดปุ่ม **Publish repository** มุมขวาบน — ตั้งชื่อ repo เช่น `money-chaser`, เลือก **Public** (ต้อง public เพื่อใช้ GitHub Actions ฟรีไม่จำกัด), **ห้ามติ๊ก** "Keep this code private" ถ้าอยากได้ quota ฟรีแบบไม่จำกัด
4. รอสักครู่ให้อัปโหลดเสร็จ (ไฟล์ `.env` จะไม่ถูกอัปโหลดเพราะกันไว้ใน `.gitignore` แล้ว ปลอดภัย)

## ตั้งค่า Secrets บน GitHub (ทำครั้งเดียว)

ไปที่หน้า repo บนเว็บ GitHub → **Settings → Secrets and variables → Actions → New repository secret** เพิ่มทีละตัว (ค่าจริงอยู่ในไฟล์ `.env` ของคุณ):

- `LITELLM_BASE_URL`
- `LITELLM_KEY_1`
- `LITELLM_KEY_2`
- `GROQ_API_KEY`
- `HL_MAIN_ADDRESS`

(ยังไม่ต้องเพิ่ม `HL_AGENT_PRIVATE_KEY` ตอนนี้ — จะเพิ่มตอนที่ถึงขั้นเทรดจริงใน P6 เพื่อลดความเสี่ยงที่ไม่จำเป็นตอนนี้)

## รันทดสอบครั้งแรกบน GitHub (ตรงนี้คือจุดที่ verify ได้จริง)

1. ไปที่แท็บ **Actions** ของ repo
2. เลือก workflow ชื่อ **"Test Setup (manual)"** ทางซ้าย
3. กด **Run workflow** (ปุ่มสีเขียว) → รอ ~1 นาที
4. เข้าไปดูผล — ควรเห็น ✅ ครบทั้ง 3 จุด (Hyperliquid, LiteLLM, Groq) ถ้าติด ❌ ตรงไหนส่ง log มาให้ผมดูได้เลย

## ความปลอดภัย (ย้ำจาก BUILD-SPEC.md)

- `HL_AGENT_PRIVATE_KEY` ต้องเป็น **agent wallet (API wallet)** ที่ถอนเงินออกไม่ได้เท่านั้น ห้ามใช้ private key ของ wallet หลัก
- อย่า commit ไฟล์ `.env` เด็ดขาด (มีอยู่ใน `.gitignore` แล้ว)
