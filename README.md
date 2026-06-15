# Boss Hunter Discord Bot

บอท Discord สำหรับคำนวณเวลาเกิดบอสจากปุ่มกดใน channel ตามแนวทางใน `BossHunter.md`

คู่มือสำหรับสมาชิกพร้อมรูปภาพประกอบ: [docs/user-guide-th.md](docs/user-guide-th.md)

## สิ่งที่ทำได้

- เลือกบอสจากเมนูเมื่อบอสตาย แล้วบอทคำนวณเวลาเกิดรอบถัดไป
- มีแผงจัดกลุ่มเป้าหมายล่าบอส ให้สมาชิกแต่ละคนเลือกบอสเข้ากลุ่มส่วนตัวได้สูงสุด 5 ตัว
- ส่ง DM แจ้งเตือนบอสเกิดให้สมาชิกที่มีบอสตัวนั้นอยู่ในกลุ่มเป้าหมาย
- รองรับบอสหลายตัวด้วยระบบแบ่งหน้า หน้า ละ 25 ตัว
- แสดงเวลาแบบ Discord timestamp ให้แต่ละคนเห็นตาม timezone ของตัวเอง
- แสดง Server ช่องเกิด ธาตุ และเผ่าของบอสในเมนู สถานะ และข้อความแจ้งเตือน
- แจ้งเตือนใน channel ตอนครบกำหนดเวลาเกิด โดยขึ้นข้อความว่าบอสเริ่มเข้าเฟส
- ข้อความแจ้งเตือนจะแสดงคำแนะนำ `กดปุ่มบันทึกเวลาคูลดาวน์ หลังบอสตาย` และมีปุ่ม `บันทึกเวลา` ให้กรอกเวลาเค้าดาวน์แบบ `HH:MM`
- หลังบันทึกจากข้อความแจ้งเตือน ระบบจะเพิ่มข้อความยืนยันไว้ใต้ alert เดิมและแสดงสมาชิก Discord ที่กดบันทึก
- ถ้าแจ้งเตือนแล้วเกิน `UNRECORDED_BOSS_RESET_MINUTES` นาทีแล้วยังไม่มีคนบันทึกบอสตาย ระบบจะตั้ง `cooldown_minutes` เป็น `0`, ปิดปุ่มบันทึกเวลา และเพิ่มปุ่ม `ตั้งค่าคูลดาวน์`; หลังตั้งค่าสำเร็จปุ่มนี้จะถูกปิดเพื่อกันกดซ้ำ
- ถ้าบอสตัวไหน `cooldown_minutes` เป็น `0` ระบบจะเตือนให้ตั้งค่าคูลดาวน์ตามรอบ `MISSING_COOLDOWN_REMINDER_HOURS`
- เก็บสถานะเวลาเกิดไว้ใน `boss_config.json` และเก็บกลุ่มเป้าหมายสมาชิกไว้ใน `boss_groups.json`
- เพิ่มบอสใหม่ผ่านคำสั่ง Discord ได้

## ติดตั้ง

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

แก้ไฟล์ `.env`

```env
DISCORD_TOKEN=token ของบอท
DISCORD_CHANNEL_ID=id ของ channel ที่ให้บอทแจ้งเตือน
DISCORD_AUSTEJA_CHANNEL_ID=1514443568100540537
DISCORD_LAIMA_CHANNEL_ID=0000000000000000001
DISCORD_JURATE_CHANNEL_ID=0000000000000000002
DISCORD_TEST_CHANNEL_ID=id ของ channel สำหรับทดสอบบน local
EXTRA_BOSS_SERVERS=Test
DISCORD_GUILD_ID=id ของ server เพื่อให้ slash commands ขึ้นเร็ว
COMMAND_PREFIX=!
ALERT_GRACE_MINUTES=5
MISSING_COOLDOWN_REMINDER_HOURS=12
UNRECORDED_BOSS_RESET_MINUTES=45
MISSING_COOLDOWN_ALERTS_PER_PAGE=10
```

เปิด Developer Portal ของ Discord แล้วเปิด `MESSAGE CONTENT INTENT` ให้บอทด้วย ถ้าต้องการใช้คำสั่งแบบ prefix เช่น `!setup_boss`

## รันบอท

```powershell
python bot.py
```

## Storage

ระบบใช้ SQLite เป็น storage หลักในไฟล์ `bot.db`

- `boss_config.json`, `boss_groups.json`, `message_delete_queue.json` ใช้เป็นข้อมูลต้นทางสำหรับ migration ครั้งแรก
- หลังจากมีข้อมูลใน `bot.db` แล้ว บอทจะอ่านและเขียนข้อมูลจาก SQLite
- ควร backup `bot.db` ก่อนอัปเดตระบบหรือย้ายเครื่อง

## การแสดงผลคำสั่ง

คำสั่ง Slash /... จะแสดงผลแบบส่วนตัวเฉพาะคนใช้คำสั่ง ส่วนคำสั่ง Prefix !... จะส่งผลลัพธ์ทาง DM และกด reaction ที่ข้อความคำสั่ง

## คำสั่งใน Discord

- `/help` แสดงคู่มือคำสั่งทั้งหมด
- `!help` แสดงคู่มือคำสั่งทั้งหมด
- `!setup_boss Austeja` ส่งแผงจัดกลุ่มเป้าหมายล่าบอส Server Austeja ทาง DM เฉพาะคนพิมพ์
- `/setup_boss server:Austeja` แสดงแผงจัดกลุ่มเป้าหมายล่าบอสเฉพาะ Server แบบส่วนตัว
- `!boss_status Austeja` ดูเวลาเกิดรอบถัดไปของบอสเฉพาะ Server Austeja แบบตารางในข้อความเดียว
- `/boss_status server:Austeja` ดูเวลาเกิดรอบถัดไปของบอสเฉพาะ Server Austeja แบบส่วนตัว
- `!boss_status Jurate 2` หรือ `!boss_status 2 Jurate` ดูสถานะหน้าที่ 2 ของ Server Jurate
- `!boss_info boss_lv80_ch1_austeja` ดูรายละเอียดบอสจาก boss_id ทาง DM
- `/boss_info boss_id:boss_lv80_ch1_austeja` ดูรายละเอียดบอสจาก boss_id แบบส่วนตัว
- `!active_boss_alerts Austeja` ดูบอสที่เกิดแล้วและกำลังรอบันทึกเวลา พร้อมเมนูเลือกบอสทาง DM
- `/active_boss_alerts server:Austeja` ดูบอสที่เกิดแล้วและกำลังรอบันทึกเวลา พร้อมเมนูเลือกบอสแบบส่วนตัว
- ในข้อความแจ้งเตือนบอสเกิด สามารถกดปุ่ม `ดูรายการที่กำลังรอบันทึกทั้งหมด` เพื่อเปิดแผงรวมโดยไม่ต้องพิมพ์คำสั่ง
- `!set_cooldown_boss Jurate 2` แอดมินส่งข้อความตั้งค่าคูลดาวน์ของบอส Server Jurate ที่ `cooldown_minutes = 0` ไปยังห้องที่ใช้คำสั่ง ครั้งละ 10 รายการต่อหน้า
- `/set_cooldown_boss server:Jurate page:2` แอดมินส่งข้อความตั้งค่าคูลดาวน์ของบอส Server Jurate ที่ `cooldown_minutes = 0` ไปยังห้องที่ใช้คำสั่ง ครั้งละ 10 รายการต่อหน้า
- `!find_boss_spawn` ดูบอสที่ใกล้เวลาเกิดที่สุด 5 ตัว โดยส่งผลลัพธ์ทาง DM
- `/find_boss_spawn` ดูบอสที่ใกล้เวลาเกิดที่สุด 5 ตัว แบบส่วนตัวเฉพาะคนใช้คำสั่ง
- `!boss_killed` เปิดเมนูเลือกบอสที่ตายแล้ว
- `/boss_killed` เปิดเมนูเลือกบอสที่ตายแล้ว
- `!boss_killed_id boss_lv80_ch1` บันทึกว่าบอสตายด้วย id โดยไม่ต้องใช้เมนู
- `/boss_killed_id boss_id:boss_lv80_ch1` บันทึกว่าบอสตายด้วย id โดยไม่ต้องใช้เมนู
- `!add_boss boss_lv100 480 Austeja | CH-1 | ไฟ | มังกร | บอสเวล 100 (Phoenix)` เพิ่มบอสใหม่พร้อม server ช่องเกิด ธาตุ และเผ่า แล้วส่งผลลัพธ์ทาง DM
- `/add_boss boss_id:boss_lv100 cooldown_minutes:480 name:บอสเวล 100 (Phoenix) server:Austeja channel:CH-1 element:ไฟ race:มังกร` เพิ่มบอสใหม่พร้อม server ช่องเกิด ธาตุ และเผ่า แล้วแสดงผลแบบส่วนตัว
- `/edit_boss boss_id:boss_lv100_ch1_austeja name:ชื่อใหม่ server:Austeja channel:CH-1 element:ไฟ race:มังกร cooldown_minutes:480` แก้ไขข้อมูลบอสแบบส่วนตัว
- `/delete_boss boss_id:boss_lv100_ch1_austeja` ลบข้อมูลบอสแบบส่วนตัว
- ระบบจะสร้าง id จริงตาม server และ channel เช่น `boss_lv100_ch1_austeja`, `boss_lv100_ch1_laima`
- `!clear_boss boss_lv80_ch1` ล้างเวลาที่บันทึกไว้ของบอส
- `/clear_boss boss_id:boss_lv80_ch1` ล้างเวลาที่บันทึกไว้ของบอส
- `/set_alert_channel server:Austeja channel:#boss-austeja` ตั้ง channel ของ server Austeja
- `/set_alert_channel server:Laima channel:#boss-laima` ตั้ง channel ของ server Laima
- `/set_alert_channel server:Jurate channel:#boss-jurate` ตั้ง channel ของ server Jurate
- !set_alert_channel Austeja #boss-austeja ตั้ง channel ของ server Austeja
- `/reset_cooldown_boss` ตั้งค่า `cooldown_minutes` ของบอสทุกตัวเป็น `0`
- `/reset_cooldown_boss boss_id:boss_lv90_ch1` ตั้งค่า `cooldown_minutes` ของบอสที่ระบุเป็น `0`
- `!reset_cooldown_boss` ตั้งค่า `cooldown_minutes` ของบอสทุกตัวเป็น `0`
- `!reset_cooldown_boss boss_lv90_ch1` ตั้งค่า `cooldown_minutes` ของบอสที่ระบุเป็น `0`
- `/set_boss_cooldown boss_id:boss_lv90_ch1 countdown:01:35` ตั้งค่าคูลดาวน์ คำนวณเวลาเกิดรอบถัดไป และแสดงผลแบบส่วนตัว
- `!set_boss_cooldown boss_lv90_ch1 01:35` ตั้งค่าคูลดาวน์ คำนวณเวลาเกิดรอบถัดไป และส่งผลลัพธ์ทาง DM

หลังเพิ่มบอสใหม่ ให้ใช้ `!setup_boss Austeja` หรือ `/setup_boss server:Austeja` เพื่อเปิดแผงจัดกลุ่มใหม่ที่มีรายการล่าสุด

## Server ของบอส

ระบบรองรับรายชื่อบอสแยกตาม server:

- `Austeja` เป็นค่า default
- `Laima`
- `Jurate`

บอสเดิมทั้งหมดถูกตั้งเป็น `Austeja` และ boss id รูปแบบใหม่จะมี server ต่อท้าย เช่น `boss_lv80_ch1_austeja`, `boss_lv80_ch1_laima` หรือ `boss_lv80_ch1_jurate`

สำหรับทดสอบบน local สามารถเพิ่ม server พิเศษผ่าน `.env` ได้ เช่น:

```env
EXTRA_BOSS_SERVERS=Test
DISCORD_TEST_CHANNEL_ID=1514443568100540537
```

ถ้า production ไม่ตั้ง `EXTRA_BOSS_SERVERS` server `Test` จะไม่แสดงใน Slash Command

## จัดกลุ่มเป้าหมายล่าบอส

สมาชิกใช้คำสั่งนี้เพื่อเปิดแผงจัดกลุ่มของตัวเอง:

```text
/setup_boss server:Austeja
```

ระบบจะแสดงแผงแบบส่วนตัวเฉพาะคนที่ใช้คำสั่ง สมาชิกสามารถเลือกบอสเข้ากลุ่มเป้าหมายส่วนตัวได้สูงสุด 5 ตัว รายชื่อบอสเรียงตามเลเวลและแบ่งหน้าเพราะ Discord แสดงได้สูงสุด 25 รายการต่อเมนู

ในแผงมีปุ่ม:

- เมนูเลือกบอส: เพิ่มบอสเข้ากลุ่มเป้าหมายของผู้กด
- `เพิ่ม 5 ตัวใกล้เกิด`: ตั้งกลุ่มเป็นบอสที่ใกล้เวลาเกิดที่สุด 5 ตัว
- `ดูกลุ่มของฉัน`: แสดงรายชื่อบอสในกลุ่มส่วนตัว
- `ล้างกลุ่ม`: ล้างรายชื่อบอสในกลุ่มส่วนตัว
- ปุ่ม `หน้า .../...`: เปลี่ยนหน้าเมื่อมีบอสเกิน 25 ตัว

เมื่อบอสที่อยู่ในกลุ่มของสมาชิกเริ่มเข้าเฟส ระบบจะส่ง DM แจ้งเตือนไปหาเจ้าของกลุ่มนั้น

ถ้าต้องการแผงบันทึกเวลาบอสตาย ให้ใช้:

```text
/boss_killed
```

## ข้อความแจ้งเตือนบอสเกิด

เมื่อครบกำหนดเวลาเกิด บอทจะส่งข้อความแจ้งเตือนใน channel พร้อมปุ่ม โดยหัวข้อความจะแสดง channel ของบอส เช่น `⚠️ บอส แมพเลเวล 120 CH2 เริ่มเข้าเฟส (5 minutes ago)`:

- `บันทึกเวลา`: กดหลังจากฆ่าบอสตัวนั้น แล้วกรอกเวลาเค้าดาวน์รูปแบบ `HH:MM`
- หลังบันทึกสำเร็จ บอทจะเพิ่มผลการบันทึกไว้ใต้ข้อความแจ้งเตือนเดิม แสดงข้อความ `ข้อความนี้กำลังจะถูกลบในอีก 1 นาที` ปิดปุ่มเพื่อกันกดซ้ำ และลบข้อความแจ้งเตือนนั้นหลัง 1 นาที

ตัวอย่างเวลา:

- `01:35` = 1 ชั่วโมง 35 นาที
- `00:45` = 45 นาที
- `02:00` = 2 ชั่วโมง

เวลาที่กรอกจะถูกบันทึกเป็น `cooldown_minutes` ของบอสตัวนั้น และนำไปคำนวณเวลาเกิดรอบถัดไปทันที

ถ้าบอทเพิ่งรีสตาร์ทหรือพลาดช่วงเวลาเล็กน้อย ระบบจะยังส่งแจ้งเตือน `เริ่มเข้าเฟส` ภายในช่วง `ALERT_GRACE_MINUTES`

ถ้าส่งแจ้งเตือนแล้วไม่มีใครกดบันทึกบอสตายภายใน 45 นาที หรือตามค่า `UNRECORDED_BOSS_RESET_MINUTES` ระบบจะตั้ง `cooldown_minutes` ของบอสตัวนั้นเป็น `0`, ล้างเวลาเกิดที่ค้างไว้, ปิดปุ่มบันทึกเวลา และเพิ่มปุ่ม `ตั้งค่าคูลดาวน์` บนข้อความแจ้งเตือนเดิมเพื่อให้ตั้งเวลาใหม่ได้ทันที หลังตั้งค่าสำเร็จปุ่ม `ตั้งค่าคูลดาวน์` จะถูกปิดเพื่อกันกดซ้ำจากข้อความเดิม แสดงข้อความ `ข้อความนี้กำลังจะถูกลบในอีก 1 นาที` และลบข้อความแจ้งเตือนนั้นหลัง 1 นาที

## แจ้งเตือนเมื่อลืมตั้งค่าคูลดาวน์

ถ้าบอสตัวไหนมีค่า:

```json
"cooldown_minutes": 0
```

ระบบจะส่งข้อความแจ้งเตือนไปยัง channel แจ้งเตือนตามรอบ `MISSING_COOLDOWN_REMINDER_HOURS` พร้อมปุ่ม:

- `ตั้งค่าคูลดาวน์`: กดเพื่อกรอกเวลา `HH:MM` และแสดงผลสำเร็จแบบส่วนตัวเฉพาะคนที่กดปุ่ม หลังตั้งค่าสำเร็จบอทจะปิดปุ่มบนข้อความเตือนเดิม แสดงข้อความ `ข้อความนี้กำลังจะถูกลบในอีก 1 นาที` และลบข้อความนั้นหลัง 1 นาที

ถ้ายังไม่มีใครตั้งค่า และ `cooldown_minutes` ยังเป็น `0` อยู่ ระบบจะส่งเตือนซ้ำทุก 12 ชั่วโมง หรือตามค่า `MISSING_COOLDOWN_REMINDER_HOURS`

บอทจะไม่ส่งข้อความแจ้งเตือน `cooldown_minutes = 0` ทันทีตอนสตาร์ทโปรแกรมแล้ว

## ถ้า `!setup_boss` แล้วไม่มีอะไรเกิดขึ้น

ให้ลองใช้ `/setup_boss server:Austeja` ก่อน เพราะ Slash Command ไม่ต้องพึ่ง `MESSAGE CONTENT INTENT`

ตรวจสิ่งเหล่านี้:

- บอทรันอยู่ และ terminal แสดง `Boss Hunter bot is online as ...`
- เปิด `MESSAGE CONTENT INTENT` แล้ว ถ้าจะใช้คำสั่งแบบ `!`
- `COMMAND_PREFIX` ใน `.env` เป็น `!`
- สมาชิกสามารถใช้ `setup_boss` ได้ ไม่ต้องมีสิทธิ์ `Manage Server`
- บอทมีสิทธิ์ `View Channel`, `Send Messages`, `Embed Links`
- ตอนเชิญบอท ต้องเลือก scope `bot` และ `applications.commands`
- ตั้ง `DISCORD_GUILD_ID` เป็น server id แล้วรีสตาร์ทบอท เพื่อให้ slash commands ขึ้นเร็ว

ถ้าระบบแจ้งเตือนไม่ส่ง ให้ใช้คำสั่งนี้ใน channel ที่ต้องการให้แจ้งเตือน:

```text
/set_alert_channel
```

บอทจะส่งข้อความทดสอบใน channel นั้นทันที ถ้าส่งไม่ได้ให้เพิ่มสิทธิ์ `View Channel`, `Send Messages`, `Embed Links` ให้บอท

## ตั้งค่าบอส

แก้ไข `boss_config.json` ได้โดยตรง เช่น

```json
{
  "boss_lv80_ch1": {
    "name": "บอสเวล 80 (Dragon)",
    "channel": "CH-1",
    "element": "ไฟ",
    "race": "มังกร",
    "cooldown_minutes": 240,
    "next_spawn": null,
    "alert_sent": false
  }
}
```







