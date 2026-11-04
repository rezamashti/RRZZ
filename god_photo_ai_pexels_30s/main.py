import os
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from pathlib import Path
from telegram import Update, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)
from dotenv import load_dotenv

from storage import Store
from vision import feature_vector, similarity, profile_suitability
from image_sources import ImageSources

load_dotenv()
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("god-photo-ai")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "@zrzrgod").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().isdigit()
}
POST_INTERVAL_SECONDS = max(30, int(os.getenv("POST_INTERVAL_SECONDS", "30")))
MAX_PHOTOS_PER_CYCLE = max(1, min(10, int(os.getenv("MAX_PHOTOS_PER_CYCLE", "5"))))
ROOT = Path(__file__).parent
SAMPLES_DIR = ROOT / "data" / "samples"
QUEUE_DIR = ROOT / "data" / "queue"
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_DIR.mkdir(parents=True, exist_ok=True)

store = Store(ROOT / "data" / "godbot.sqlite3")
user_modes = {}
publishing_paused = False
sources = ImageSources(ROOT / "data" / "sources")

# آستانه تشخیص تصویر خیلی مشابه؛ قابل تنظیم در .env
DUPLICATE_THRESHOLD = float(os.getenv("DUPLICATE_THRESHOLD", "0.985"))
MIN_PROFILE_SCORE = float(os.getenv("MIN_PROFILE_SCORE", "-0.10"))
MIN_STYLE_SIMILARITY = float(os.getenv("MIN_STYLE_SIMILARITY", "0.12"))


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def deny(update: Update):
    await update.effective_message.reply_text("این دستور فقط برای مدیر ربات فعاله.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("سلام! ربات God Photo AI فعاله، اما دسترسی مدیریت نداری.")
        return
    await update.message.reply_text(
        "سلام مشتی! 🤖\n\n"
        "دستورها:\n"
        "/learn - دریافت عکس‌های نمونه برای یادگیری سبک\n"
        "/queue - دریافت عکس‌هایی که اجازه انتشار دارند\n"
        "/status - وضعیت نمونه‌ها و صف انتشار\n"
        "/postnow - انتشار یک عکس همین الان\n"
        "/discovernow - جست‌وجوی خودکار عکس مشابه نمونه‌ها\n"
        "/pause - توقف انتشار خودکار\n"
        "/resume - ادامه انتشار خودکار\n"
        "/stopmode - توقف دریافت عکس نمونه\n\n"
        "تحلیل تصویری CLIP فعال است. اگر عکس بفرستی، به‌صورت پیش‌فرض به نمونه‌های سبک اضافه می‌شود. پاسخ‌گویی هوشمند متنی غیرفعال است."
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"شناسه عددی تلگرام شما: {update.effective_user.id}")


async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await deny(update)
    user_modes[update.effective_user.id] = "learn"
    await update.message.reply_text(
        "حالت یادگیری روشن شد. عکس‌های نمونه را همین‌جا بفرست. "
        "مدل CLIP از محتوای بصری و سبک آن‌ها بردار ویژگی می‌سازد. پایان: /stopmode"
    )


async def queue_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await deny(update)
    user_modes[update.effective_user.id] = "queue"
    await update.message.reply_text(
        "حالت صف انتشار روشن شد. فقط عکس‌هایی را بفرست که اجازه انتشارشان را داری. "
        "مدل آن‌ها را از نظر شباهت به سبک نمونه‌ها رتبه‌بندی می‌کند. پایان: /stopmode"
    )


async def stopmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await deny(update)
    user_modes[update.effective_user.id] = "off"
    await update.message.reply_text("دریافت عکس خاموش شد. برای فعال‌کردن دوباره /learn را بزن.")


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    # By default, incoming photos teach the desired style; /queue is only for manual candidates.
    mode = user_modes.get(uid, "learn")
    if mode == "off":
        await update.message.reply_text("برای افزودن عکس نمونه، /learn را بزن.")
        return

    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    target_dir = SAMPLES_DIR if mode == "learn" else QUEUE_DIR
    target = target_dir / f"{photo.file_unique_id}.jpg"
    await tg_file.download_to_drive(custom_path=str(target))

    added = store.add_image(str(target), mode, photo.file_unique_id)
    if not added:
        target.unlink(missing_ok=True)
        await update.message.reply_text("این عکس قبلاً ثبت شده بود؛ تکراری ذخیره نشد.")
        return

    try:
        vec = feature_vector(target)
        store.set_features(photo.file_unique_id, vec)
        if mode == "learn":
            await update.message.reply_text(
                f"نمونه ذخیره و تحلیل شد ✅ ({store.count('learn')} نمونه)"
            )
        else:
            score, label = profile_suitability(target)
            await update.message.reply_text(
                f"عکس تحلیل و وارد صف شد ✅\n"
                f"برداشت مدل: {label}\n"
                f"امتیاز تناسب تقریبی: {score:.3f}\n"
                f"تعداد صف: {store.count('queue')}"
            )
    except Exception as exc:
        log.exception("Image analysis failed")
        store.delete_image(photo.file_unique_id)
        target.unlink(missing_ok=True)
        await update.message.reply_text(
            "تحلیل عکس ناموفق بود و فایل به صف اضافه نشد. "
            "اتصال اینترنت برای دانلود اولیه مدل و فضای کافی را بررسی کن."
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await deny(update)
    await update.message.reply_text(
        f"وضعیت God Photo AI\n"
        f"مدل بینایی: CLIP محلی\n"
        f"نمونه‌های سبک: {store.count('learn')}\n"
        f"عکس‌های صف انتشار: {store.count('queue')}\n"
        f"کانال مقصد: {CHANNEL_ID}\n"
        f"فاصله انتشار: هر {POST_INTERVAL_SECONDS} ثانیه\n"
        f"منابع جست‌وجو: Wikimedia Commons فعال + Pexels اختیاری\n"
        f"حداقل شباهت سبک: {MIN_STYLE_SIMILARITY:.2f}\n"
        f"انتشار خودکار: {'متوقف' if publishing_paused else 'فعال'}"
    )


def choose_next():
    samples = store.get_images("learn")
    candidates = store.get_images("queue")
    if not candidates:
        return None

    sample_vectors = [r["features"] for r in samples if r.get("features")]
    if not sample_vectors:
        return candidates[0]

    dims = len(sample_vectors[0])
    valid = [v for v in sample_vectors if len(v) == dims]
    if not valid:
        return candidates[0]
    target = [sum(v[i] for v in valid) / len(valid) for i in range(dims)]

    # جلوگیری از انتشار دوباره یا بسیار مشابه با تصاویر قبلاً منتشرشده
    posted_vectors = store.get_posted_features()
    ranked = []
    for row in candidates:
        vec = row.get("features")
        if not vec:
            continue
        score = similarity(target, vec)
        duplicate = any(similarity(vec, old) >= DUPLICATE_THRESHOLD for old in posted_vectors)
        if duplicate or score < MIN_STYLE_SIMILARITY:
            continue
        try:
            profile_score, _ = profile_suitability(row["path"])
        except Exception:
            profile_score = 0.0
        if profile_score < MIN_PROFILE_SCORE:
            continue
        ranked.append((score + profile_score * 0.15, row))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1] if ranked else None


async def publish_one(context: ContextTypes.DEFAULT_TYPE, notify_chat=None):
    row = choose_next()
    if not row:
        if notify_chat:
            await context.bot.send_message(
                notify_chat,
                "فعلاً عکس مناسبی در صف نیست. نمونه‌های سبک را با ارسال عکس برای ربات بیشتر کن؛ جست‌وجوی خودکار در چرخه بعدی انجام می‌شود."
            )
        return
    path = Path(row["path"])
    if not path.exists():
        store.delete_image(row["unique_id"])
        return
    try:
        meta_path = path.with_suffix(path.suffix + ".json")
        meta = {}
        if meta_path.exists():
            try:
                import json
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        caption = "🌿\nعکاس/سازنده: " + str(meta.get("photographer", "Contributor")) + "\nمجوز: " + str(meta.get("license", "Provider license")) + "\nمنبع: " + str(meta.get("source_url", ""))
        with path.open("rb") as f:
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=InputFile(f, filename=path.name),
                caption=caption
            )
        store.mark_posted(row["unique_id"])
        if notify_chat:
            await context.bot.send_message(notify_chat, "عکس در کانال آزمایشی منتشر شد. ✅")
    except Exception as exc:
        log.exception("Publishing failed")
        if notify_chat:
            await context.bot.send_message(notify_chat, f"انتشار ناموفق بود: {exc}")


async def postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await deny(update)
    await publish_one(context, update.effective_chat.id)


async def discover_images(notify_message=None):
    """Search for images using the visual style learned from the user's samples."""
    samples = store.get_images("learn")
    sample_vectors = [r["features"] for r in samples if r.get("features")]
    if not sample_vectors:
        if notify_message:
            await notify_message.reply_text("اول چند عکس نمونه بفرست تا سبک موردنظرت رو تحلیل کنم.")
        return 0

    from vision import suggest_search_queries
    queries = suggest_search_queries(sample_vectors, limit=3)
    items = await sources.fetch(queries, limit=MAX_PHOTOS_PER_CYCLE)
    added = 0
    import json
    for item in items:
        path = Path(item["path"])
        if not store.add_image(str(path), "queue", item["unique_id"]):
            continue
        path.with_suffix(path.suffix + ".json").write_text(
            json.dumps({k: item.get(k, "") for k in ("source_url", "photographer", "license", "license_url")}, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            vec = feature_vector(path)
            store.set_features(item["unique_id"], vec)
            added += 1
        except Exception:
            log.exception("CLIP analysis failed for photo %s", item["unique_id"])
            store.delete_image(item["unique_id"])
            path.unlink(missing_ok=True)
    if notify_message:
        await notify_message.reply_text(
            f"جست‌وجوی خودکار انجام شد. عبارت‌های انتخاب‌شده بر اساس نمونه‌ها: {', '.join(queries)}\n"
            f"{added} عکس دریافت و تحلیل شد؛ صف انتشار: {store.count('queue')}"
        )
    return added


async def discover_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await deny(update)
    await update.effective_message.reply_text("دارم بر اساس عکس‌های نمونه دنبال تصاویر مشابه می‌گردم…")
    try:
        await discover_images(update.effective_message)
    except Exception as exc:
        log.exception("Automatic image discovery failed")
        await update.effective_message.reply_text(
            f"جست‌وجو ناموفق بود: {type(exc).__name__}: {str(exc)[:250]}"
        )


async def scheduled_cycle(context: ContextTypes.DEFAULT_TYPE):
    global publishing_paused
    if publishing_paused:
        return
    # Do not scrape/fetch blindly: wait for the user to provide a visual reference first.
    if store.count("learn") and store.count("queue") < MAX_PHOTOS_PER_CYCLE:
        try:
            await discover_images()
        except Exception:
            log.exception("Scheduled autonomous image discovery failed")
    await publish_one(context)


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global publishing_paused
    if not is_admin(update.effective_user.id):
        return await deny(update)
    publishing_paused = True
    await update.effective_message.reply_text("انتشار و جست‌وجوی خودکار متوقف شد. برای ادامه /resume را بزن.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global publishing_paused
    if not is_admin(update.effective_user.id):
        return await deny(update)
    publishing_paused = False
    await update.effective_message.reply_text("انتشار خودکار دوباره فعال شد. ✅")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"God Photo AI is running")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server running on port %s", port)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN در فایل .env تنظیم نشده است.")
    if not ADMIN_IDS:
        log.warning("ADMIN_USER_IDS is empty; send /myid to the bot, then configure it in .env and restart.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CommandHandler("queue", queue_mode))
    app.add_handler(CommandHandler("stopmode", stopmode))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("postnow", postnow))
    app.add_handler(CommandHandler("discovernow", discover_now))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(MessageHandler(filters.PHOTO, receive_photo))

    app.job_queue.run_repeating(
        scheduled_cycle,
        interval=POST_INTERVAL_SECONDS,
        first=POST_INTERVAL_SECONDS,
        name="style-search-and-publish",
    )
    log.info("God Photo AI started; channel=%s; Commons enabled", CHANNEL_ID)
    start_health_server()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
