# คู่มือการใช้งาน BossHunter Discord Bot

เวอร์ชันล่าสุดสำหรับระบบ BossHunter ที่รองรับ `Austeja`, `Laima` และ `Jurate`

> คู่มือนี้สรุปการใช้งานสำหรับสมาชิกและแอดมิน พร้อมภาพประกอบและคำสั่งสำคัญ

## ภาพรวมระบบ

![ภาพรวมระบบ](images/01-workflow.svg)

- BossHunter เป็น Discord bot สำหรับติดตามเวลาบอสเกิด แยกข้อมูลตาม server และ channel ของบอส
- ระบบแจ้งเตือนเมื่อบอสเริ่มเข้าเฟส และมีปุ่มให้บันทึกคูลดาวน์หลังบอสตาย
- ข้อมูลหลักเก็บใน SQLite ไฟล์ `bot.db` ควรสำรองไฟล์นี้ก่อนอัปเดตหรือย้ายเครื่อง
- ระบบรองรับ server: `Austeja`, `Laima`, `Jurate` โดยค่าเริ่มต้นคือ `Austeja`

## เริ่มต้นใช้งาน

![แผงจัดกลุ่ม](images/02-group-panel.svg)

ใช้คำสั่งนี้เพื่อเปิดแผงจัดกลุ่มเป้าหมายล่าบอสแบบส่วนตัว:

```text
/setup_boss server:Austeja
/setup_boss server:Laima
/setup_boss server:Jurate
```

ถ้าใช้คำสั่งแบบ prefix:

```text
!setup_boss Austeja
!setup_boss Jurate
```

ระบบจะส่งแผงไปทาง DM ของผู้ใช้คำสั่ง

## จัดกลุ่มเป้าหมายล่าบอส

![เพิ่ม 5 ตัวใกล้เกิด](images/03-upcoming-group.svg)

- สมาชิก 1 คนเลือกบอสเข้ากลุ่มเป้าหมายส่วนตัวได้สูงสุด 5 ตัว
- รายชื่อบอสในเมนูจะแสดงเฉพาะ server ที่เลือกใน `/setup_boss`
- ปุ่ม `เพิ่ม 5 ตัวใกล้เกิด` จะเลือกบอสที่ใกล้เวลาเกิดที่สุด 5 ตัว
- ถ้ายังไม่มีบอสที่ตั้งเวลาเกิด ระบบจะสุ่มบอส 5 ตัวของ server นั้นให้แทน
- เมื่อบอสในกลุ่มเริ่มเข้าเฟส ระบบจะส่ง DM แจ้งสมาชิกเจ้าของกลุ่ม

## แจ้งเตือนบอสเกิด

![แจ้งเตือนบอสเกิด](images/04-spawn-alert.svg)

เมื่อถึงเวลาเกิด ระบบจะส่งข้อความใน channel ของ server นั้น เช่น:

```text
⚠️ บอส แมพเลเวล 120 CH2 เริ่มเข้าเฟส (5 minutes ago)
เวลาเกิด: Tuesday, 9 June 2026 14:45
Server: Jurate | ช่องเกิด: 2 | ธาตุ: น้ำ | เผ่า: กลายพันธุ์
กดปุ่มบันทึกเวลาคูลดาวน์ หลังบอสตาย
[บันทึกเวลา]
```

หลังบันทึกเวลาแล้ว ระบบจะ:

- แสดงผู้บันทึก
- คำนวณเวลาเกิดรอบถัดไป
- ปิดปุ่มเพื่อกันกดซ้ำ
- เพิ่มข้อความ `ข้อความนี้กำลังจะถูกลบในอีก 1 นาที`
- ลบข้อความแจ้งเตือนหลัง 1 นาที

ในข้อความแจ้งเตือนบอสเกิดมีปุ่ม `ดูรายการที่กำลังรอบันทึกทั้งหมด` สำหรับเปิดแผงรวมทันทีโดยไม่ต้องพิมพ์คำสั่ง เหมาะกับกรณีมีบอสเกิดหลายตัวและต้องการเลือกบอสที่ตายแล้วจากรายการเดียว

ถ้าแจ้งเตือนแล้วเกิน 45 นาทีแต่ยังไม่มีคนบันทึก ระบบจะตั้ง `cooldown_minutes = 0` ให้บอสตัวนั้น และแสดงปุ่มตั้งค่าคูลดาวน์แทน

## บันทึกคูลดาวน์หลังบอสตาย

![บันทึกคูลดาวน์](images/05-record-flow.svg)

เวลาคูลดาวน์ใช้รูปแบบ `HHMM` หรือ `HH:MM`

ตัวอย่าง:

```text
0030 = 30 นาที
01:35 = 1 ชั่วโมง 35 นาที
00:45 = 45 นาที
02:00 = 2 ชั่วโมง
00:06 = 6 นาที
```

คำสั่งตั้งคูลดาวน์โดยตรง:

```text
/set_boss_cooldown boss_id:boss_lv90_ch1_austeja countdown:0030
!set_boss_cooldown boss_lv90_ch1_austeja 01:35
```

คำสั่งส่งข้อความให้ตั้งคูลดาวน์ของบอสที่ `cooldown_minutes = 0`:

```text
/set_cooldown_boss server:Jurate page:1
!set_cooldown_boss Jurate 2
```

คำสั่งนี้ใช้ได้เฉพาะแอดมิน และส่งครั้งละ 10 รายการต่อหน้า

## ดูสถานะและค้นหาบอส

![แผนผังคำสั่ง](images/06-command-map.svg)

ดูตารางสถานะบอสเฉพาะ server:

```text
/boss_status server:Austeja page:1
/boss_status server:Jurate page:2
!boss_status Jurate 2
!boss_status 2 Jurate
```

ดูรายละเอียดบอสจาก `boss_id`:

```text
/boss_info boss_id:boss_lv80_ch1_austeja
!boss_info boss_lv80_ch1_austeja
```

ดูบอสที่เกิดแล้วและกำลังรอบันทึกเวลาในแผงเดียว:

```text
/active_boss_alerts server:Austeja
/active_boss_alerts server:Jurate page:1
!active_boss_alerts Austeja
```

ในแผงนี้สามารถเลือกบอสจากเมนูเพื่อกรอกเวลาคูลดาวน์หลังบอสตายได้ทันที เหมาะสำหรับห้องที่มีข้อความแจ้งเตือนหลายรายการและต้องการหาบอสที่ยังไม่ได้บันทึกให้เร็วขึ้น

อีกวิธีที่ง่ายกว่า คือกดปุ่ม `ดูรายการที่กำลังรอบันทึกทั้งหมด` ในข้อความแจ้งเตือนบอสเกิด ระบบจะเปิดแผงเดียวกันแบบส่วนตัวทันที

ดูบอสที่ใกล้เวลาเกิดที่สุด 5 ตัว:

```text
/find_boss_spawn
!find_boss_spawn
```

ผลลัพธ์ของคำสั่งดูข้อมูลจะแสดงแบบส่วนตัว หรือส่งทาง DM สำหรับคำสั่ง prefix

## คำสั่งสำหรับสมาชิกทั่วไป

```text
/help
/setup_boss server:Austeja
/boss_status server:Austeja page:1
/boss_info boss_id:boss_lv80_ch1_austeja
/active_boss_alerts server:Austeja
/find_boss_spawn
/boss_killed
/boss_killed_id boss_id:boss_lv80_ch1_austeja
```

## คำสั่งสำหรับแอดมิน

```text
/add_boss boss_id:boss_lv100 cooldown_minutes:480 name:ชื่อบอส server:Austeja channel:CH-1 element:ไฟ race:มังกร
/edit_boss boss_id:boss_lv100_ch1_austeja name:ชื่อใหม่ server:Austeja channel:CH-1 element:ไฟ race:มังกร cooldown_minutes:480
/delete_boss boss_id:boss_lv100_ch1_austeja
/clear_boss boss_id:boss_lv80_ch1_austeja
/set_alert_channel server:Jurate channel:#boss-jurate
/reset_cooldown_boss
/reset_cooldown_boss boss_id:boss_lv90_ch1_austeja
/set_boss_cooldown boss_id:boss_lv90_ch1_austeja countdown:01:35
/set_cooldown_boss server:Jurate page:1
```

## ข้อควรจำสำหรับ Production

- ห้ามนำ `.env` หรือ `DISCORD_TOKEN` ขึ้น GitHub
- ควร backup `bot.db` เป็นประจำ เพราะเป็นฐานข้อมูลหลักของระบบ
- บน production ควรรันด้วย `systemd` หรือ process manager และตั้ง restart อัตโนมัติ
- ระบบใช้ rotating log เพื่อลดปัญหา `bot.log` โตไม่หยุด
- ถ้าใช้ `set_cooldown_boss` กับบอสจำนวนมาก ให้ใช้ `page` เพื่อลดการส่งข้อความจำนวนมากในครั้งเดียว
