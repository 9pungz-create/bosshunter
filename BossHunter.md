เขียนบอทเองด้วย Python (ได้ฟังก์ชันตามสั่ง 100%)หากคุณต้องการเขียนบอทขึ้นมาใช้เอง เพื่อให้มี ระบบปุ่มกดคำนวณรอบถัดไป และ ตั้งค่าคูลดาวน์แยกตามเลเวลบอส ได้อย่างอิสระ สามารถใช้โค้ดตัวอย่างภาษา Python (discord.py) ด้านล่างนี้ไปรันได้เลยครับ:
1. ไฟล์โครงสร้างข้อมูลบอส (boss_config.json)สร้างไฟล์นี้เพื่อบันทึกชื่อบอส เลเวล และระยะเวลาเกิดใหม่ (หน่วยเป็นนาที):json{
  "boss_lv80": {
    "name": "บอสเวล 80 (Dragon)",
    "cooldown_minutes": 240,
    "next_spawn": null
  },
  "boss_lv90": {
    "name": "บอสเวล 90 (Demon)",
    "cooldown_minutes": 360,
    "next_spawn": null
  }
}
2. โค้ดบอสบอท (bot.py)pythonimport discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta

# ตั้งค่า Intents ของบอท
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# โหลดข้อมูลบอส
with open("boss_config.json", "r", encoding="utf-8") as f:
    boss_data = json.load(f)

class BossView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # ปุ่มอยู่ถาวรไม่หมดอายุ

    @discord.ui.button(label="💀 บอสเวล 80 ตายแล้ว", style=discord.ButtonStyle.danger, custom_id="kill_boss_80")
    async def boss_80_killed(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_boss_time(interaction, "boss_80")

    async def update_boss_time(self, interaction: discord.Interaction, boss_id: str):
        now = datetime.now()
        cooldown = boss_data[boss_id]["cooldown_minutes"]
        next_spawn_time = now + timedelta(minutes=cooldown)
        
        # บันทึกเวลาเกิดใหม่
        boss_data[boss_id]["next_spawn"] = next_spawn_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # สร้าง Timestamp สไตล์ Discord สำหรับแสดงเวลาในเครื่องของผู้ใช้ทุกคนอัตโนมัติ
        timestamp_string = f"<t:{int(next_spawn_time.timestamp())}:F>"
        relative_string = f"<t:{int(next_spawn_time.timestamp())}:R>"

        await interaction.response.send_message(
            f"🎯 บันทึกเวลาตายแล้ว! **{boss_data[boss_id]['name']}** จะเกิดใหม่ในรอบถัดไปตอน:\n"
            f"📅 เวลา: {timestamp_string} ({relative_string})", 
            ephemeral=False
        )

# ลูปตรวจสอบและแจ้งเตือนก่อนบอสเกิด 10 นาที
@tasks.loop(minutes=1)
async def check_boss_spawns():
    channel = bot.get_channel(123456789012345678) # 🛑 ใส่ ID ห้อง Discord ที่ต้องการให้บอทแจ้งเตือน
    if not channel:
        return
        
    now = datetime.now()
    for boss_id, info in boss_data.items():
        if info["next_spawn"]:
            spawn_time = datetime.strptime(info["next_spawn"], "%Y-%m-%d %H:%M:%S")
            # ถ้าเหลือเวลาอีก 10 นาทีจะเกิด
            if now <= spawn_time <= now + timedelta(minutes=10):
                await channel.send(f"⚠️ **⚠️ ALERT:** {info['name']} กำลังจะเกิดในอีกไม่ช้า! เตรียมตัวให้พร้อม!")
                info["next_spawn"] = None # รีเซ็ตเพื่อไม่ให้เตือนซ้ำ

@bot.event
async def on_ready():
    print(f"บอท {bot.user.name} ออนไลน์แล้ว!")
    bot.add_view(BossView()) # ลงทะเบียนปุ่มกดให้ทำงานได้ตลอดเวลา
    check_boss_spawns.start()

@bot.command()
async def setup_boss(ctx):
    """คำสั่งสำหรับแอดมิน เพื่อส่งข้อความที่มีปุ่มกดลงในห้อง"""
    embed = discord.Embed(title="⚔️ ระบบบันทึกเวลาและแจ้งเตือนบอส", description="เมื่อบอสตาย ให้มากดปุ่มด้านล่างเพื่อคำนวณเวลาเกิดในรอบถัดไป", color=0xff0000)
    await ctx.send(embed=embed, view=BossView())

bot.run("YOUR_BOT_TOKEN") # 🛑 ใส่ Token ของบอทคุณตรงนี้