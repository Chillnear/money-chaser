# Money Chaser: แผนพิสูจน์กำไรสุทธิภายใต้งบรวม $100

วันที่ 2026-09-05 — เอกสารส่งต่องาน ไม่ใช่คำสั่งเปิดเงินจริงหรือเพิ่มทุน

## 1. เป้าหมายและขอบเขต

หากต้องการรับประกันไม่ขาดทุน ห้ามเปิดสถานะเสี่ยงในระบบนี้ ไม่มีโมเดลหรือ stop-loss รับประกันเงินต้นได้ เป้าหมายที่ทดสอบได้คือค้นหากลยุทธ์ที่มีกำไรสุทธิหลังต้นทุนทั้งหมด ภายใต้ความเสี่ยงที่กำหนด และยอมจบด้วย NO-GO หากไม่มีหลักฐาน ห้ามเพิ่มความถี่หรือ leverage เพียงเพื่อเร่งกำไร

งบ $100 หมายถึงเงินลงทุน + API + infrastructure + transfer fees ทั้งหมด ไม่ใช่ $100 แล้วบวกต้นทุนภายนอกไม่จำกัด ไม่เติมเงินชดเชยการขาดทุน ข้อเสนอจัดสรร: $70 สำรองนอกระบบเทรด, $25 สำหรับ live pilot เฉพาะเมื่อผ่านเกณฑ์, $5 สำหรับค่าใช้จ่ายทั้งหมด เงินจำลอง $28.56 ไม่ถือเป็นเงินที่มีอยู่จริงจนกว่าจะยืนยัน mode และบัญชี ผู้ใช้ต้องยืนยันวงเงินขาดทุนที่รับได้ก่อน live

## 2. หลักฐานและสิ่งที่ยังไม่ทราบ

- origin/main ณ การตรวจเป็น a892527; workflow ยังใช้ checkout@v4/setup-python@v5 และ LITELLM keys เดิม ภาพ Success ของ c44baab เป็นรอบเก่า ไม่ยืนยัน Mimi migration
- local มี commit e90239d และงาน Mimi ที่ยังไม่ commit; ห้าม reset/overwrite งานค้าง ต้องตรวจ diff และ reconcile กับ bot state ก่อนรวม
- health 2026-09-05: analyst_macro/Gemini 2.5 pro content=None; roles อื่นผ่าน ยังไม่ทราบ root cause จริง ข้อความ error ที่อ้าง filter เป็นการคาดเดาใน wrapper ไม่ใช่หลักฐาน provider
- scripts/check_role_health.py ใช้ output_token_cap=50, max_validation_retries=0; อาจไม่พอกับ reasoning model ต้องตรวจ finish_reason และ usage เพื่อพิสูจน์
- main.py เปิด baseline เมื่อ health ไม่ผ่าน; src/baseline.py ลด risk multiplier เป็น 0.5 แต่ sizing floor ทำให้ไม้ ZEC $10/stop 6% เสี่ยง $0.60 หรือ 2.10% ของ $28.56 ก่อนต้นทุน เทียบเป้าหมาย baseline 1% = $0.2856
- SL 963.51/entry 1025.01/TP 1148.01 คิดเป็นประมาณ -6%/+12%; กำไรขั้นต้นเต็ม TP เพียง $1.20 และขาดทุนขั้นต้นที่ SL $0.60 ไม่ใช่ผลตอบแทนแน่นอน
- config เดิมเปิดให้ leverage 3, risk/trade 2%, min-notional override 4%, drawdown breaker 25%, LLM hard stop $15/month; ไม่เหมาะนำมาใช้กับวัตถุประสงค์รักษาทุนขนาดเล็กโดยไม่ทบทวน
- rule_backtest ที่บันทึกไว้: trend -$3.83/184 trades, mean-reversion -$3.14/97 trades, funding-carry +$0.91/181 trades จากทุน $28; ต้อง audit assumptions ก่อนเปรียบเทียบ ไม่ถือ carry เป็น arbitrage จนตรวจว่าป้องกัน delta จริง
- backtest 180 วันอีกชุด: 12 trades, -$1.77; grid hourly walk-forward BTC -$1.12 จาก $28 หลักฐานยังไม่ผ่านสำหรับ live
- Mimi เคยรายงาน 36 model IDs และทดสอบโจทย์ JSON สั้น 5 ตัวผ่าน ข้อมูลนี้เป็น availability/smoke evidence ไม่ใช่ quality benchmark หรือการยืนยัน underlying model identity
- ข้อความ LINE ไม่แสดง mode จึงยังใช้ข้อความนี้ยืนยันการเปิดเงินจริงไม่ได้

## 3. ลำดับการส่งมอบ

ทำ P0 -> P1 -> P2 -> P3 -> P4 ตาม dependency ไม่เปิด live ระหว่างทาง ทุก phase ส่ง diff, ผลตรวจ, ข้อจำกัด และ GO/NO-GO แยกกัน งานนี้ไม่ได้อนุมัติเปลี่ยนสถานะ ZEC ปัจจุบัน

### P0: ตรวจสถานะจริงและปิดช่องทางเสี่ยงเมื่อระบบเสื่อม

ไฟล์: src/main.py, src/risk/sizing.py, src/execution/*, src/report/*, tests ที่เกี่ยวข้อง

1. ตรวจ GitHub vars MODE, workflow effective mode, broker ที่ถูกสร้าง และ state/account reconciliation แบบ read-only ไม่อ่าน/แสดง key; LINE/dashboard ต้องมี PAPER/LIVE/SHADOW, commit SHA, strategy ID, credential-profile label, health timestamp
2. เสนอและ implement นโยบายใหม่: health/ข้อมูลวิกฤตไม่ผ่าน -> งดเปิดไม้ใหม่; จัดการ SL/TP และ reconciliation ของไม้เดิมต่อเสมอ baseline เปิดได้เฉพาะ shadow จนผ่าน promotion gate แยกของตนเอง ห้าม early return ก่อน manage existing position
3. ขนาดสถานะ: risk_budget = equity * risk_fraction; expected_loss = notional * (stop_fraction + roundtrip_fee_fraction + slippage_buffer) + adverse_funding_estimate. ปัด quantity ลงตาม exchange precision แล้วคำนวณใหม่ หาก minimum notional ทำให้เกินงบ -> SKIP_MIN_NOTIONAL_RISK ห้ามยก risk cap หรือบีบ stop ให้ order ผ่าน
4. ห้าม clamp stop ให้แคบกว่าจุด invalidation ที่กลยุทธ์ต้องการเพื่อเพิ่มขนาดไม้; หาก stop ที่ต้องใช้ไม่เข้า policy -> skip
5. เก็บ incident ของ ZEC นี้: intended risk, actual risk, floor adjustment, strategy source และ gate decisions เพื่อพิสูจน์ regression
6. ทดสอบ health fail + existing position, stale report, missing report, min-notional floor, fees, quantity rounding, stop rejection, duplicate daily run, retry/order idempotency

เกณฑ์ผ่าน: health ล้มแล้วไม่มี new-entry; ไม้เดิมยังมี protection; ทุก order ผ่าน risk แบบรวมต้นทุน; LINE ระบุ mode ชัด

### P1: Mimi migration และ health check ที่เชื่อถือได้

ไฟล์: src/settings.py, src/agents/llm.py, scripts/check_role_health.py, scripts/probe_models.py, .github/workflows/*

1. Review งาน MIMI_COACH_KEY ที่ค้างอยู่ ใช้ secret ใหม่อย่างเดียวเมื่อเลือก Mimi ไม่หมุนกลับ key เก่าที่ entitlement ต่างกันโดยเงียบ เก็บ legacy profile ไว้ rollback แบบ explicit
2. เพิ่ม endpoint profile ที่ชัด เช่น MIMI_COACH_BASE_URL คู่กับ MIMI_COACH_KEY; ห้ามส่ง Mimi key ไป LITELLM_BASE_URL เดิมจนยืนยันว่าเป็นปลายทางเดียวกันและได้รับอนุญาต ห้ามพิมพ์ env/config ที่มี credential หรือเก็บ key ใน git, report, clipboard/log
3. ให้ผู้ใช้ตั้ง GitHub Secrets ผ่านช่องทางลับ ตรวจได้เฉพาะชื่อและผล authenticated request ถ้าเข้าถึงคีย์ไม่ได้ให้รายงาน blocker ห้ามดึง credential จาก MCP ออกมาเอง
4. ตรวจ allowed models ด้วย credential/endpoint ที่ production ใช้จริง รายชื่อ global /models ไม่พอ ต้อง role-schema request สำเร็จด้วย จัด candidate list จากผลปัจจุบันเท่านั้น
5. Health แบ่ง availability และ task-schema validation. จับ metadata แบบ redact: requested/returned model, finish_reason, content present, reasoning usage, latency, status, retries. อย่าเรียก content=None ว่า content filter ถ้าไม่มีหลักฐาน
6. ตรวจ token budget แบบ bounded: ทดลองงบที่เหมาะกับ provider เช่น 256/1024 โดยจำกัดราคาและเวลารวม แยก reasoning budget ถ้า API รองรับ Retry transient/parse ได้สูงสุด 1 ครั้งพร้อม backoff; 401/403 หยุด, 429 เคารพ Retry-After และหยุดรอบ ไม่สลับ key เพื่อฝ่า limit
7. ตัวเลือกสำหรับทดสอบ: Qwen 3.8 Max (trend), Sonnet 5 (positioning), Gemini 3.1 Pro (macro), GPT 5.6 Terra หรือ GPT 5.5 (redteam), Opus 4.8 หรือ vertex_ai/claude-opus-5 (judge). IDs เหล่านี้เป็นชื่อจาก gateway ที่เคยพบ ต้องตรวจใหม่ ไม่ auto-promote จากเลขรุ่น
8. ใช้ prompt จริงหลายสถานการณ์และ schema จริง; ห้าม health ping 1 ครั้งเป็นหลักฐานพร้อมใช้ทั้งระบบ ทดสอบ local mocks + CI + authenticated smoke ใน paper
9. ค่า cost=0 ที่ provider ไม่รายงานให้ถือ unknown ไม่ใช่ฟรี ใช้ rate card ที่ยืนยันหรือ prepaid allowance หากไม่ทราบราคาไม่ทำ batch ใหญ่

เกณฑ์ผ่าน: CI ใช้ Mimi profile ที่ยืนยัน, ทุกบทบาทตอบ task schema ได้, ไม่มี secret leak, rollback documented, health fail งด new-entry ตาม P0

### P2: บัญชีต้นทุนและ simulator ก่อนค้นหากลยุทธ์

ไฟล์: src/store/*, src/report/dashboard.py, scripts/backtest*.py, src/execution/broker_paper.py, src/shadow_grid.py

1. แยก trading PnL / funding / exchange fees / slippage / API / infrastructure / transfer / deposits. net_economic_profit ต้องหักทั้งหมด แยกเงินจำลองจากเงินจริงและ shadow ทุกกลยุทธ์
2. Audit signal timestamp และ closed candles; signal ที่ close t execute ได้ตั้งแต่ t+1 เท่านั้น ไม่ใช้ high/low ก่อนเวลา entry ในการ hit SL/TP
3. ใช้ข้อมูล 1h สำหรับ fill/exit; ถ้า SL และ TP แตะในแท่งเดียวให้ worst-case stop first หรือใช้ละเอียดขึ้น ห้ามเลือกเส้นทางที่กำไรที่สุด
4. Inventory conservation, pending-order capital reservation, funding ตามช่วงถือจริง, spread และ market impact; grid order ทุกระดับต้องผ่าน minimum size/precision; ห้ามถือว่าจับคู่ spot/perp ได้หาก venue ไม่มี spot asset นั้น
5. ตรวจ historical universe/survivorship, missing/stale candles, delisting, funding/OI point-in-time; ห้ามใช้ OI ปัจจุบันเติมอดีต ห้ามใช้ global normalization ที่เห็นข้อมูล test
6. Funding บน Hyperliquid ชำระรายชั่วโมง ต้องตรวจหน่วย annualized/8h/hour ใน code และ fixture ทุกจุด; delta-neutral carry ต้องมีขาทั้งสอง, fees สองขา, basis/unwind risk และ margin budget
7. เปรียบเทียบต้นทุน base กับ stress 2x, latency/partial-fill cases; โมเดลเงินทุนใช้ actual budget ไม่เทียบผลจากการใส่เงินใหม่ทุกรอบโดยไม่บอก

เกณฑ์ผ่าน: conservation tests, no-lookahead tests, cost reconciliation และ replay trade ที่ตรวจมือได้ตรงกัน ผลเก่าที่ไม่ผ่านให้ label INVALID ไม่เอาไปเลือกผู้ชนะ

### P3: แข่งขันกลยุทธ์แบบจำกัดการลอง

เริ่ม deterministic 4h/daily signal + hourly execution ลดค่า AI; วัดเร็วด้วย backtest ที่ซื่อตรง ไม่เพิ่มความถี่เทรดเพื่อให้ได้ sample เร็ว

1. Baselines: cash/no-trade และ buy-and-hold ที่ใช้ capital/cost เดียวกัน
2. Candidates จำกัดสาม family: trend breakout/pullback บนสินทรัพย์สภาพคล่องสูง, range mean-reversion พร้อม trend veto, delta-neutral funding carry เฉพาะที่ขนาดทุน/สองขาทำได้จริง grid เป็น challenger shadow และต้องผ่าน order-size constraints ก่อน
3. กำหนด universe BTC/ETH/SOL เป็นชุดเริ่มวิจัย ตรวจ venue/liquidity/history จริงก่อนใช้; ไม่เลือก ZEC/HYPE/PUMP เพราะเห็นผลย้อนหลังดีแล้วกำหนดย้อนหลัง
4. บันทึก experiment manifest ก่อนรัน: data hashes, timestamp split, universe, parameters, fees, budget, seed. จำกัดไม่เกิน 12 parameter sets ต่อ family; นับทุก trial รวมผลเสีย
5. ใช้ข้อมูลอย่างน้อย 12 เดือนหากมีคุณภาพพอ; walk-forward train 120d/test 30d แบบไม่ทับกัน; purge/embargo ตาม maximum holding horizon; กันช่วงล่าสุด 60d เป็น final holdout ที่เปิดดูครั้งเดียว
6. ถ้าปรับหลังเห็น final holdout ต้องเรียกช่วงนั้น development และหา unseen period ใหม่ ห้ามรายงานว่า OOS เดิมยังอิสระ
7. LLM ย้อนหลังอาจรู้เหตุการณ์อนาคตจาก training; ใช้ replay เป็น consistency test เท่านั้น ไม่ถือเป็นหลักฐาน causal edge. วัด AI-added-value จริงด้วย forward shadow เทียบ deterministic บน timestamp/data เดียวกัน
8. metrics: net economic PnL, maximum drawdown, expectancy net/trade, profit factor, turnover, exposure, loss tails, cost/profit ratio และ block-bootstrap confidence interval ของ expectancy (preserve temporal dependence)

Provisional promotion gates (เป็นเงื่อนไขวิจัย ไม่ใช่การรับประกัน): >=100 closed OOS trades ที่ไม่อาศัย overlapping samples เป็นอิสระ; net expectancy >0 และ lower 95% block-bootstrap bound >0; profit factor >=1.2; positive net result ภายใต้ 2x execution costs; max drawdown <=5%; ไม่ให้ trade เดียวสร้าง >25% ของกำไรรวม; ผ่าน final holdout. ถ้าตัวอย่างไม่พอให้ INCONCLUSIVE ห้ามลดเกณฑ์เพื่อให้ผ่าน

### P4: Forward paper แล้วจึงเสนอ live pilot

1. เลือก champion จาก P3; freeze parameters แล้ว forward paper อย่างน้อย 30 วันและ >=30 closed trades (ถึงช้าก็รอ ไม่บังคับ churn). Model-health uptime >=99% เป็นเป้าหมายปฏิบัติการ ไม่ใช่หลักฐานกำไร
2. AI challenger ใช้เฉพาะ candidates ที่มี deterministic trigger; เทียบ approve/veto/no-AI และบันทึก opportunity cost ของ veto ห้ามเพิ่ม 5-agent calls ทุกชั่วโมง ใช้ cache และจำกัดค่าใช้จ่ายรวม $5
3. ทดสอบ duplicate scheduler, restart, API timeout, disconnect, stale price, missing protective order, partial fills และ reconciliation. GitHub cron ไม่ใช่ตัวเฝ้า stop แบบ realtime; live ต้องมี exchange-native reduce-only protection และตรวจ order ID/status จริง
4. ก่อน live ให้ผู้ใช้อนุมัติสถานะจริงและวงเงินขาดทุน; ข้อเสนอ pilot capital $25, risk/trade 0.5% ของ active equity รวมต้นทุน, notional <= active equity, one position, no averaging-down/martingale, no leverage boost, daily stop 1%, weekly stop 2%, peak drawdown stop 5%, total pilot loss budget $1.25. Thresholds เป็นเป้าหมายสั่งหยุดและอาจถูกทะลุเมื่อ gap/slippage
5. หาก $25 ทำ order ขั้นต่ำโดยไม่เกิน risk ไม่ได้ ผลที่ถูกต้องคือไม่เปิด live ไม่เพิ่มทุนโดยอัตโนมัติ เงินสำรอง $70 ไม่ใช่ trading equity สำหรับขยายไม้
6. ห้ามย้าย config/risk.yaml โดยอัตโนมัติ: ไฟล์มี human-only rule ให้นำเสนอ diff ของค่าที่เสนอและรอผู้ใช้อนุมัติก่อนใช้จริง. ดูแล protective orders ของไม้เดิมแม้ breaker หยุด new-entry
7. หากหลัง P3 ไม่มีผู้ผ่าน ให้ส่ง NO-GO พร้อมหลักฐานและคงเงินสำรอง ถือว่าเสร็จงานวิจัย ไม่ให้ไปหากลยุทธ์เสี่ยงขึ้นต่อจนมีผลบวก

## 4. แผนเวลาและการส่งต่องาน

- ช่วงงานแรก 1–2 วันโดยประมาณ: P0/P1 และ replay incident; ขึ้นกับ secret/endpoint access และ review
- ช่วงถัดไป 2–5 วันโดยประมาณ: P2/P3; หาก data ไม่พอให้ระบุ blocker ไม่ fabricate
- Forward 30 วันขึ้นไป: P4; ระยะเวลาไม่ใช่ deadline ให้เริ่ม live
- ส่งเอกสาร audit, migration checklist, experiment manifest, results.csv, replay examples, model_eval report และ GO_NO_GO.md ที่อ้างหลักฐาน. ห้ามแก้บทเรียน/threshold อัตโนมัติจากผลไม่กี่ไม้

## 5. Prompt สำหรับโมเดลผู้ลงมือ

อ่าน IMPLEMENTATION_PLAN_100USD.md แล้วเริ่ม P0 เท่านั้นก่อน เคารพงานค้างใน working tree และตรวจ origin/main ล่าสุด ยืนยัน mode แบบ read-only แก้ health-failure new-entry policy และ minimum-notional risk overflow พร้อม regression tests โดยไม่เปิด/ปิดสถานะจริง ไม่อ่านหรือเผยแพร่คีย์ ไม่แก้ risk.yaml ที่ human-only โดยไม่ได้อนุมัติ ส่งสาเหตุ incident, diff, test results และรายการที่ยังไม่ยืนยัน แล้วดำเนิน P1–P3 ตาม dependencies เก็บต้นทุนงานทั้งหมดในงบ $5 ห้ามถือ MCP smoke เป็น production migration สำเร็จ หรือถือผลย้อนหลัง LLM เป็นหลักฐานกำไรอิสระ จบด้วย GO/NO-GO/INCONCLUSIVE ตามเกณฑ์ โดยไม่เปิด live อัตโนมัติ

## 6. แหล่งอ้างอิงสำหรับยืนยัน implementation

- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining

ตรวจเอกสารปัจจุบันและค่าที่บัญชีใช้จริงก่อน implement; stop, funding carry, grid และ stablecoin balance ล้วนมีความเสี่ยง ไม่มีช่องใดให้รับประกันเงินต้น
