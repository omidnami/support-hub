# معماری Support Hub، سرویس بله و چت

این سند مرجع مشترک Support Hub و پلنت است. هدف آن روشن کردن مرز مسئولیت‌ها، وضعیت اتصال‌های Socket.IO، مدل هویت، جریان پیام بله، ثبت ربات توسط مدیر پلنت و محدودیت‌های نسخه فعلی است.

> برای مدل‌ها و ایجنت‌های کدنویسی، فایل [`AGENTS.md`](../AGENTS.md) حافظه اجرایی پروژه، تصمیم‌های قطعی، نقشه فایل‌ها و چک‌لیست تغییرات را نگه می‌دارد. این سند توضیح انسانی و معماری است؛ `AGENTS.md` مرجع سریع برای شروع کار است.

> اصل حاکم: **Support Hub فقط لایه انتقال است.** پیام مشتری، تاریخچه گفتگو، تخصیص اپراتور، وضعیت آنلاین اپراتورها و تصمیم‌گیری درباره پاسخ هم‌زمان، در پلنت انجام می‌شود. Hub نه تاریخچه پیام ذخیره می‌کند و نه تعداد اپراتورها را می‌داند.

---

## ۱. خلاصه اجرایی

در نسخه فعلی سه کانال داریم:

1. **بله**: مشتری در بله پیام می‌دهد. سرویس مستقل `bale_service` آپدیت را از بله می‌گیرد، به Hub روی Socket.IO می‌فرستد و پاسخ اپراتور را از Hub می‌گیرد و با `sendMessage` به بله می‌فرستد. کلاینت بله هرگز به Socket.IO وصل نمی‌شود.
2. **چت داخلی**: مشتری با هویت امضاشده مستقیم به Hub وصل می‌شود. Hub پیام را به اتاق پروژه و اتاق همان گفتگو می‌رساند.
3. **سایر پیام‌رسان‌ها**: در آینده با همان قرارداد پیام و همان مدل پل/آداپتور اضافه می‌شوند. فعلاً فقط ثبت منبع و اعتبارسنجی توکن آن‌ها وجود دارد.

اتصال‌های اصلی:

```text
مشتری بله
   │  (Bale API)
   ▼
سرویس بله ──────── Socket.IO ────────► Support Hub ◄──── Socket.IO ──── پلنت / اپراتور
   │                                     │
   └── MySQL: خواندن sources بله         └── MySQL: users / projects / sources
       و ثبت webhook_secret/last_update_id
                                              ▲
                                              │ Socket.IO با امضای مشتری
                                         چت داخلی مشتری
```

- مدیر پلنت ربات بله را با `POST /api/sources` در Hub ثبت می‌کند.
- Hub توکن ربات را با `getMe` اعتبارسنجی می‌کند و `robot_id` را ذخیره می‌کند.
- سرویس بله به‌صورت دوره‌ای منابع `bale` را از دیتابیس می‌خواند، برای هر منبع یک secret وب‌هوک می‌سازد و `setWebhook` را روی بله انجام می‌دهد.
- سرویس بله با یک اتصال سرویس‌به‌سرویس (bridge) به Hub وصل می‌شود. این اتصال یک اپراتور نیست و به اتاق `bridge:bale` می‌رود.
- اپراتورهای پلنت با توکن پروژه به Hub وصل می‌شوند. Hub فقط می‌داند پیام مربوط به کدام پروژه است؛ نمی‌داند چند اپراتور وجود دارد.

---

## ۲. اجزا و مرز مسئولیت‌ها

| موضوع | Support Hub | سرویس بله | پلنت |
| --- | --- | --- | --- |
| احراز هویت اپراتور | با توکن پروژه | ❌ | توکن را نگه می‌دارد و اتصال را مدیریت می‌کند |
| احراز هویت مشتری بله | ❌ | از روی وب‌هوک همان ربات | ❌ |
| احراز هویت مشتری چت داخلی | با امضای کوتاه‌عمر | ❌ | کلید امضا را در اختیار دارد (اگر پلنت واسط باشد) |
| مدیریت اپراتور، نقش، شیفت | ❌ | ❌ | ✅ |
| تخصیص گفتگو | ❌ | ❌ | ✅ |
| هم‌زمانی چند اپراتور | ❌ | ❌ | ✅ |
| تاریخچه پیام | ❌ | ❌ | ✅ (یا هر سیستم ثبت دیگری) |
| پیام وقتی اپراتور آفلاین است | ارسال به webhook پروژه | ❌ | ✅ ذخیره، تخصیص و نمایش بعدی |
| دریافت آپدیت بله | ❌ | ✅ | ❌ |
| ارسال پاسخ به بله | ❌ | ✅ | ❌ |
| دریافت فایل/رسانه (getFile) | ✅ فقط proxy و احراز هویت پروژه | ✅ getFile، دانلود و کش موقت | ❌ |
| ذخیره دائمی فایل | ❌ | ❌ (فقط کش موقت با TTL) | ✅ (اختیاری، S3/دیسک) |
| ثبت ربات | ✅ (API ثبت منبع) | ❌ | مدیر پلنت درخواست را می‌زند |
| نگهداری توکن ربات | ✅ در `sources.token` | برای فراخوانی بله می‌خواند | ❌ |
| نگهداری `webhook_secret` و `last_update_id` | ✅ در `sources` | می‌نویسد | ❌ |
| نگهداری متن/فایل پیام | ❌ | ❌ | ✅ |

---

## ۳. وضعیت فعلی Socket.IO

### ۳.۱. نقش‌ها

Hub سه نقش اتصال دارد:

| نقش | احراز هویت | اتاق | کاربرد |
| --- | --- | --- | --- |
| `operator` | `token` پروژه در `auth` | `project:{project_id}` | پلنت/داشبورد اپراتور |
| `client` | امضای `robot_id + user_id + expires` | `chat:{project}:{source}:{robot}:{user}` | مشتری چت داخلی که مستقیم به Hub وصل می‌شود |
| `bridge` | `HUB_BALE_SERVICE_TOKEN` مشترک | `bridge:bale` | سرویس بله |

اتاق‌ها:

- `project:{project_id}`: همه اتصال‌های اپراتور همان فروشگاه.
- `bridge:bale`: اتصال سرویس بله (سرویس‌به‌سرویس).
- `chat:{project_id}:{source}:{robot_id}:{user_id}`: یک گفتگوی مشخص برای کلاینت مستقیم.

اتاق فروشگاه از توکن احرازشده ساخته می‌شود، نه از `project_id` ورودی. هیچ پیامی نمی‌تواند پروژه‌اش را عوض کند.

### ۳.۲. وضعیت اتصال و reconnect

- Hub اتصال‌ها را در RAM نگه می‌دارد. با ری‌استارت Hub همه اتصال‌ها قطع می‌شوند.
- Heartbeat پیش‌فرض Engine.IO فعال است: Ping هر ۲۵ ثانیه، مهلت Pong بیست ثانیه.
- سرویس بله در قطعی Hub به‌صورت خودکار با backoff دوباره وصل می‌شود.
- سرویس بله فقط یک نسخه فعال باید داشته باشد. Hub اتصال bridge دوم برای `bale` را رد می‌کند تا پاسخ‌ها دوبار به بله نرسند.
- Hub فعلاً Redis adapter ندارد؛ بنابراین باید یک پروسه Hub اجرا شود. چند worker بدون Redis، اتاق‌ها را بین پروسه‌ها به اشتراک نمی‌گذارد.

### ۳.۳. قرارداد پیام

همه کانال‌ها یک ساختار هفت‌فیلدی دارند:

```json
{
  "user_id": "101",
  "sender": "client",
  "text": "سلام",
  "file": null,
  "robot_id": "123456789",
  "timestamp": 1794841200000,
  "source": "bale"
}
```

| فیلد | معنی |
| --- | --- |
| `user_id` | شناسه گفتگوی مشتری؛ برای بله `message.chat.id` |
| `sender` | `client` برای پیام مشتری، `operator` برای پاسخ اپراتور |
| `text` | متن پیام؛ حداکثر ۱۰٬۰۰۰ کاراکتر در Hub |
| `file` | `null`، رشته یا شیء JSON؛ Hub فایل را ذخیره نمی‌کند |
| `robot_id` | شناسه ربات/منبع در پلتفرم؛ برای بله `getMe.result.id` |
| `timestamp` | Unix timestamp بر حسب میلی‌ثانیه |
| `source` | نوع منبع: `bale`، `chat`، ... |

`chat_id` و `conversation_key` جزء این هفت فیلد نیستند؛ metadata لایهٔ انتقال‌اند. `chat_id` شناسهٔ گفتگو در کانال است و برای بله همان `message.chat.id` است. جزئیات در بخش ۸.۴.

`file` برای همهٔ providerها یک شکل ثابت دارد (`provider`، `type`، `file_id`، `file_name`، `mime_type`، `file_size`، ...). URL موقت provider داخل پیام قرار نمی‌گیرد؛ کلاینت فایل را با `GET /api/files` از Hub می‌گیرد. برای `chat` که فایل روی Planet است، `url` مجاز است. جزئیات در بخش ۱۲.

قواعد مسیر:

| مبدأ | `sender` | مسیر در Hub |
| --- | --- | --- |
| اپراتور، `source=chat` | `operator` | اتاق پروژه + اتاق گفتگو |
| اپراتور، `source=bale` | `operator` | اتاق پروژه + اتاق `bridge:bale` |
| سرویس بله (ورودی مشتری) | `client` | اتاق پروژه |
| کلاینت چت داخلی | `client` | اتاق پروژه + اتاق گفتگو |

- اگر سرویس بله قطع باشد، Hub پاسخ اپراتور به منبع `bale` را با `chat_error` رد می‌کند؛ به این ترتیب اپراتور پیام «ارسال‌شده» نمی‌بیند.
- تحویل به بله در این نسخه best-effort است. ACK نهایی از Bale به پلنت برنمی‌گردد. برای delivery status دقیق، در فاز بعد یک رویداد جداگانه لازم است.
- اگر اپراتوری آنلاین نباشد، Hub پیام ورودی را در جایی صف نمی‌کند؛ اگر webhook پروژه تنظیم شده باشد آن را با HMAC به Planet می‌فرستد و فقط با پاسخ 2xx ACK موفق می‌دهد. بدون webhook، پیام با `store_offline` رد می‌شود. جزئیات در بخش ۸.

### ۳.۴. محدودیت‌ها

- پیام هر اتصال اپراتور/کلاینت: ۳۰ پیام در ۱۰ ثانیه.
- پیام سرویس بله: سقف بالاتر و مخصوص bridge؛ برای محافظت از Hub اعمال می‌شود.
- `file` حداکثر ۶۵٬۵۳۶ بایت پس از سریال‌سازی JSON.
- متن حداکثر ۱۰٬۰۰۰ کاراکتر.
- پیام خالی (بدون متن و فایل) پذیرفته نمی‌شود.

---

## ۴. سیستم احراز هویت

### ۴.۱. مدیر/پلنت

- هر فروشگاه یک `project` دارد.
- توکن پروژه ریشه دسترسی است و فقط در بک‌اند پلنت نگهداری می‌شود.
- اتصال اپراتور با `auth: {token: PROJECT_TOKEN}` انجام می‌شود.
- اپراتورها توکن جداگانه در Hub ندارند. چند اپراتور می‌توانند از یک اتصال منطقی پلنت استفاده کنند یا پلنت چند اتصال باز کند؛ Hub در هر حال فقط اتاق پروژه را می‌شناسد.
- تعویض توکن پروژه، اتصال‌های جدید را ملزم به توکن جدید می‌کند. اتصال‌های موجود تا قطع‌شدن باقی می‌مانند.

### ۴.۲. سرویس بله

- یک secret مشترک `HUB_BALE_SERVICE_TOKEN` بین Hub و سرویس بله وجود دارد.
- سرویس بله با `role=bridge`، `provider=bale` و همان secret وصل می‌شود.
- این secret نباید در مرورگر، اپ موبایل یا مخزن عمومی قرار بگیرد.
- سرویس بله به همه منابع `bale` دسترسی دارد؛ یک سرویس سطح پلتفرم است. دسترسی دیتابیس آن باید فقط به همان ردیف‌های لازم محدود شود.

### ۴.۳. مشتری بله

- مشتری هیچ حساب یا توکنی در Hub ندارد.
- هویت او از مسیر وب‌هوک و توکن همان ربات گرفته می‌شود:
  - secret داخل URL وب‌هوک → ردیف `sources`
  - `sources.project_id` → فروشگاه
  - `message.chat.id` → `user_id`
- در چت خصوصی بله، `chat.id == from.id` است. در این نسخه فقط چت خصوصی پذیرفته می‌شود. پیام گروه/کانال نادیده گرفته می‌شود تا قرارداد هفت‌فیلدی مبهم نشود.

### ۴.۴. مشتری چت داخلی

- اگر چت داخلی از طریق پلنت برود، دقیقاً مثل بله است و Hub فقط پیام آماده را relay می‌کند.
- اگر مشتری مستقیم به Hub وصل شود، باید `robot_id`، `user_id`، `expires` و `signature` را بفرستد:
  ```text
  message = robot_id + "\n" + user_id + "\n" + expires
  signature = HMAC-SHA256(bytes.fromhex(client_key), message)
  ```
- `client_key` از توکن پروژه مشتق می‌شود و فقط در بک‌اند پروژه استفاده می‌شود.
- امضا عمر کوتاه دارد و `user_id` را قفل می‌کند.

---

## ۵. ثبت ربات بله توسط مدیر پلنت

### ۵.۱. جریان

```text
مدیر فروشگاه
   │  توکن ربات را در پلنت ثبت می‌کند
   ▼
پلنت  ── POST /api/sources (Authorization: Bearer PROJECT_TOKEN) ──►  Hub
                                                                      │
                                                           getMe(token) روی Bale
                                                                      │
                                                           ذخیره sources:
                                                           name=bale
                                                           robot_id
                                                           token
                                                           project_id
                                                                      │
سرویس بله ◄── خواندن دوره‌ای sources ─────────────────────────────────┘
   │
   ├── تولید webhook_secret
   ├── setWebhook(token, https://.../webhooks/bale/<secret>)
   └── اتصال bridge به Hub
```

### ۵.۲. API ثبت

```http
POST /api/sources
Authorization: Bearer PROJECT_TOKEN
Content-Type: application/json

{
  "name": "bale",
  "token": "BOT_TOKEN",
  "project_id": 1
}
```

پاسخ موفق:

```json
{
  "status": true,
  "id": 12,
  "name": "bale",
  "robot_id": "123456789",
  "project_id": 1
}
```

- `robot_id` از `getMe` گرفته می‌شود؛ به ورودی کاربر اعتماد نمی‌شود.
- ترکیب `(name, robot_id)` یکتاست.
- توکن ربات در `sources.token` ذخیره می‌شود. امنیت دیتابیس و بکاپ بر عهده نصب‌کننده است.
- تغییر توکن با `PATCH /api/sources/{id}` انجام می‌شود و دوباره `getMe` می‌شود.
- اگر توکن یا `robot_id` بله تغییر کند، `webhook_secret` و `last_update_id` در همان تراکنش پاک می‌شوند؛ چون secret و cursor به ربات قبلی تعلق دارند. سرویس بله در sync بعدی secret جدید می‌سازد و webhook جدید ثبت می‌کند.
- حذف منبع با `DELETE /api/sources/{id}` انجام می‌شود. سرویس بله در sync بعدی webhook آن ربات را حذف می‌کند.

### ۵.۳. نکات بله

- `setWebhook` فقط `url` می‌گیرد و secret یا امضای درخواست ندارد. بنابراین امنیت وب‌هوک به URL یکتا و غیرقابل‌حدس وابسته است.
- secret داخل URL نباید در access log سرویس بله یا Reverse Proxy ثبت شود. سرویس بله access log پیش‌فرض aiohttp را غیرفعال می‌کند و مسیر webhook را در پیام‌های خطا ماسک می‌کند.
- بله فقط HTTPS روی پورت ۴۴۳ یا ۸۸ را برای webhook می‌پذیرد.
- بله ۲۰۰۰ آپدیت آخر را ۲۴ ساعت نگه می‌دارد. اگر سرویس بله بیشتر قطع باشد، پیام از دست می‌رود.
- `sendMessage` متن ۱ تا ۴۰۹۶ کاراکتر را می‌پذیرد؛ سرویس بله متن بلند را تقسیم می‌کند.
- کپشن `sendPhoto`/`sendDocument` حداکثر ۱۰۲۴ کاراکتر است؛ ادامه کپشن به‌صورت پیام متنی جداگانه ارسال می‌شود.
- `file_id` مخصوص همان ربات است و بین ربات‌ها منتقل نمی‌شود.
- `getFile` حداکثر ۲۰ مگابایت می‌دهد و لینک آن حدود یک ساعت معتبر است. در این نسخه فایل در S3 ذخیره نمی‌شود.

---

## ۶. جریان پیام بله

### ۶.۱. ورود پیام از بله

```text
مشتری بله
   │  پیام
   ▼
Bale API ── webhook HTTPS ──► bale_service
                                  │
                                  ├── پیدا کردن source از روی secret
                                  ├── dedup با update_id
                                  ├── تبدیل به قرارداد هفت‌فیلدی
                                  └── Socket.IO: emit("chat", payload, metadata)
                                              │
                                              ▼
                                         Support Hub
                                              │
                                              ├── اپراتور آنلاین: اتاق project:{project_id}
                                              └── اپراتور آفلاین: webhook امضاشده به Planet
```

- `update_id` قبل از پردازش با `last_update_id` مقایسه می‌شود.
- `last_update_id` فقط پس از ACK موفق Hub به‌روزرسانی می‌شود.
- IDهایی که ارسالشان شکست خورده تا ۲۴ ساعت در یک پنجرهٔ موقت (Redis در صورت تنظیم `HUB_REDIS_URL`، وگرنه حافظه) نگه داشته می‌شوند تا موفقیت آپدیت‌های بعدی آن‌ها را به‌اشتباه «تکراری» نکند؛ پس از retry موفق پاک می‌شوند.
- اگر Hub در دسترس نباشد یا webhook آفلاین پروژه شکست بخورد، webhook بله با خطای 503 پاسخ می‌دهد تا بله در صورت پشتیبانی دوباره تلاش کند.
- اگر Hub پیام را پردازش کند اما ACK از بین برود، ممکن است همان پیام یک‌بار دیگر به اپراتور برسد. تحویل ورودی به Hub در این نسخه at-least-once است و پلنت باید برای پیام تکراری تحمل داشته باشد.
- سرویس بله همراه پیام، metadata انتقال (`update_id` و `chat_id`) را به Hub می‌فرستد؛ این metadata به هفت فیلد اضافه نمی‌شود.
- `message.chat.id` به `user_id` و `conversation.chat_id` نگاشت می‌شود.
- `timestamp` از `message.date` (ثانیه) به میلی‌ثانیه تبدیل می‌شود.
- متن از `text` یا `caption` گرفته می‌شود.
- برای فایل‌ها فقط متادیتای امن و کوچک (نوع، `file_id`، نام، MIME، اندازه) در `file` قرار می‌گیرد؛ خود فایل در Hub ذخیره نمی‌شود.

### ۶.۲. خروج پاسخ اپراتور

```text
پلنت/اپراتور
   │  Socket.IO: emit("chat", {sender:"operator", source:"bale", ...})
   ▼
Support Hub
   │  اتاق project:{project_id} + اتاق bridge:bale
   ▼
bale_service
   │  پیدا کردن ربات از robot_id
   ├── sendMessage(token, chat_id=user_id, text)
   ├── sendPhoto/sendVoice/sendAudio/sendVideo/sendDocument در صورت وجود file
   └── مدیریت 429 و خطاهای موقت
```

- پاسخ اپراتور با `source=bale` فقط وقتی پذیرفته می‌شود که منبع در همان پروژه ثبت شده باشد.
- Hub پیام را به همه اپراتورهای پروژه و به bridge بله می‌دهد.
- اگر bridge بله آنلاین نباشد، پاسخ اپراتور با `chat_error` رد می‌شود.
- سرویس بله پیام‌های هر ربات را به ترتیب پردازش می‌کند.
- اگر توکن ربات بین دو همگام‌سازی عوض شود، خطای 401 باعث خواندن توکن تازه از دیتابیس و یک تلاش دوباره می‌شود.
- در این نسخه پاسخ به پیام خاص (`reply_to_message_id`) در قرارداد نیست.

### ۶.۳. وضعیت تحویل

- ACK فعلی فقط تا دریافت پیام توسط Hub است.
- ارسال موفق به Bale در لاگ سرویس ثبت می‌شود، اما به اپراتور برنمی‌گردد.
- برای رسید نهایی، فاز بعد باید یک رویداد `delivery` یا `chat_delivery` با شناسه پیام اضافه کند.

---

## ۷. چت داخلی و اپراتورها

- چت داخلی و بله از نظر ساختار داده یکی هستند؛ فقط لایه اتصال متفاوت است.
- اپراتورها در Hub هویت فردی ندارند. هویت آن‌ها در پلنت است.
- Hub تعداد یا هویت اپراتورها را نمی‌داند؛ فقط می‌تواند ببیند آیا اتاق `project:{id}` حداقل یک اتصال اپراتور دارد یا نه. همین سیگنال برای تصمیم webhook آفلاین استفاده می‌شود.
- Hub تخصیص گفتگو انجام نمی‌دهد.
- اگر دو اپراتور هم‌زمان پاسخ بدهند، پلنت باید ترتیب و مالکیت پاسخ را حل کند. Hub فقط پیام‌ها را به ترتیب دریافت و به bridge/اتاق مقصد می‌رساند.
- برای چت داخلی، مشتری مستقیم در اتاق گفتگو است و پیام اپراتور را دریافت می‌کند.
- وضعیت آنلاین/آفلاین هر اپراتور در Hub معنی ندارد؛ فقط وجود حداقل یک اتصال در اتاق پروژه و وضعیت bridge قابل مشاهده است. همین وجود/عدم وجود برای fallback وب‌هوک کافی است، نه برای تخصیص یا انتخاب اپراتور.

---

<a id="offline-webhook"></a>
## ۸. پیام‌های فروشگاه آفلاین و webhook پلنت

> **وضعیت:** پیاده‌سازی شده است. Hub هیچ پیام یا صفی ذخیره نمی‌کند؛ اگر اتاق پروژه خالی باشد، پیام را به webhook پروژه می‌فرستد و فقط با پاسخ 2xx موفق می‌شود. بدون webhook، پیام با خطای `store_offline` رد می‌شود.

### ۸.۱. هدف و مرز مسئولیت

- اگر هیچ اپراتوری در اتاق `project:{project_id}` نباشد، Hub پیام ورودی را به webhook همان پروژه در Planet می‌فرستد.
- Planet مسئول ذخیره، تخصیص، نمایش به اپراتور بعدی و سیاست تحویل آفلاین است.
- Hub همچنان هیچ پیام، تاریخچه یا صف دائمی ذخیره نمی‌کند.
- این قاعده برای همهٔ کانال‌ها یکسان است: بله، چت داخلی، روبیکا و آداپتورهای بعدی.

### ۸.۲. ثبت و نگهداری webhook

- webhook در سطح پروژه (فروشگاه) است، نه در سطح source؛ چون یک فروشگاه می‌تواند چند کانال داشته باشد.
- در ثبت اولیه پروژه، Planet می‌تواند `webhook_url` را ارسال کند. Hub آن را در جدول `projects` ذخیره می‌کند و `webhook_secret` می‌سازد.
- برای حفظ invariant سه‌جدولی، به‌جای جدول جدید، ستون‌های `webhook_url`، `webhook_secret` و `webhook_updated_at` به `projects` اضافه شده‌اند (migration `004_add_project_webhook.sql`).
- مسیرهای مدیریت: `POST /api/projects/{id}/webhook` برای ثبت/تعویض URL و چرخش secret، و `DELETE /api/projects/{id}/webhook` برای حذف. هر دو با توکن پروژه.
- URL باید HTTPS باشد، credential و fragment نداشته باشد و به loopback/private/link-local اشاره نکند. برای توسعه محلی می‌توان با `HUB_WEBHOOK_ALLOW_HTTP=1` و `HUB_WEBHOOK_ALLOW_PRIVATE=1` استثنا داد.
- `webhook_secret` را Hub با `secrets.token_urlsafe(32)` تولید می‌کند و فقط یک‌بار در پاسخ ثبت یا تعویض برمی‌گرداند. در هیچ GET عمومی نمایش داده نمی‌شود.
- اگر webhook تنظیم نشده باشد و اپراتوری هم آنلاین نباشد، Hub خطای `store_offline` برمی‌گرداند؛ نه ACK موفق.

### ۸.۳. قاعدهٔ مشترک مسیریابی

یک تابع مشترک در Hub (نه در آداپتورها) برای همهٔ پیام‌های ورودی:

1. پیام را اعتبارسنجی می‌کند و `project_id` را از source/session استخراج می‌کند.
2. `chat_id` را از metadata bridge (اگر فرستاده شده باشد) یا `user_id` می‌سازد و `conversation_key` را در webhook قرار می‌دهد.
3. وجود حداقل یک اتصال نقش `operator` در اتاق `project:{project_id}` را بررسی می‌کند.
4. اگر حداقل یک اپراتور آنلاین باشد، پیام را طبق رفتار فعلی emit می‌کند.
5. اگر هیچ اپراتوری آنلاین نباشد، webhook پروژه را صدا می‌زند و فقط پاسخ 2xx را موفق می‌داند.
6. اگر webhook تنظیم نشده باشد یا خطا بدهد، به فرستنده خطا می‌دهد؛ Bale پاسخ 503 می‌گیرد و `last_update_id` جلو نمی‌رود.
7. فقط بعد از موفقیت، ACK نهایی به فرستنده/bridge می‌دهد.

این منطق برای `bridge` بله، کلاینت چت داخلی و `emit_source`/آداپتورهای آینده یکی است. bridge بله می‌تواند یک آرگومان دوم اختیاری با metadata انتقال بفرستد:

```json
{"update_id": 12345, "chat_id": "501", "delivery_id": "optional-explicit-id"}
```

این metadata به هفت فیلد پیام اضافه نمی‌شود و فقط برای `delivery_id` و `conversation.chat_id` استفاده می‌شود. کلاینت چت داخلی فقط یک آرگومان می‌فرستد.

### ۸.۴. chat_id و کلید گفتگو

- `chat_id` شناسهٔ گفتگو در خود کانال است:
  - بله: `message.chat.id`
  - چت داخلی: `user_id` امضاشده
  - روبیکا/تلگرام/واتساپ/اینستاگرام: chat/thread id همان پلتفرم
- `conversation_key = (project_id, source, robot_id, chat_id)` کلید یکتای گفتگو در Hub و Planet است.
- `chat_id` به هفت فیلد پیام اضافه نمی‌شود؛ در metadata وب‌هوک می‌آید. برای چت خصوصی معمولاً `chat_id == user_id` است؛ برای گروه در آینده این دو جدا می‌شوند.
- اگر آداپتور chat_id ندارد، باید از `user_id` استفاده کند و این را در metadata مشخص کند. برای گروه/کانال این fallback کافی نیست.
- `chat_id` هرگز جای `project_id` را نمی‌گیرد و برای تعیین پروژه استفاده نمی‌شود.

### ۸.۵. قرارداد وب‌هوک

```json
{
  "event": "chat.offline",
  "project_id": 12,
  "conversation": {
    "source": "bale",
    "robot_id": "987654",
    "chat_id": "501"
  },
  "message": {
    "user_id": "501",
    "sender": "client",
    "text": "سلام",
    "file": null,
    "robot_id": "987654",
    "timestamp": 1794841200000,
    "source": "bale"
  },
  "delivery_id": "bale:987654:12345"
}
```

- `message` دقیقاً همان هفت فیلد است.
- `project_id` را Hub از منبع/اتصال استخراج می‌کند و Planet نباید آن را از payload پیام بگیرد.
- `sender` همیشه `client` است؛ پاسخ اپراتور به webhook نمی‌رود.
- `delivery_id` برای idempotency است. برای بله می‌تواند از `update_id` ساخته شود. اگر در دسترس نیست، hash پایدار از source/robot_id/chat_id/timestamp/message.
- Planet باید `delivery_id` را یکتا نگه دارد؛ دریافت تکراری باید همان 2xx را برگرداند.

### ۸.۵.۱. ساختار کامل درخواست و پاسخ

نمونهٔ درخواستی که Hub به URL ثبت‌شده در Planet می‌فرستد:

```http
POST /hooks/hub HTTP/1.1
Host: planet.example.ir
Content-Type: application/json
Content-Length: <length>
X-Hub-Event: chat.offline
X-Hub-Delivery: bale:987654:12345
X-Hub-Timestamp: 1794841200
X-Hub-Signature-256: sha256=<hex hmac>

{
  "event": "chat.offline",
  "project_id": 12,
  "conversation": {
    "source": "bale",
    "robot_id": "987654",
    "chat_id": "501"
  },
  "message": {
    "user_id": "501",
    "sender": "client",
    "text": "سلام",
    "file": null,
    "robot_id": "987654",
    "timestamp": 1794841200000,
    "source": "bale"
  },
  "delivery_id": "bale:987654:12345"
}
```

پاسخ موفق Planet باید `2xx` باشد؛ نمونه:

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"ok": true}
```

- Hub بدنهٔ پاسخ را پردازش نمی‌کند؛ فقط status را بررسی می‌کند.
- `2xx` فقط بعد از ذخیرهٔ ماندگار پیام یا ثبت آن در یک صف ماندگار مجاز است.
- `3xx` دنبال نمی‌شود و شکست محسوب می‌شود.
- `4xx`/`5xx`، timeout یا خطای اتصال یعنی شکست؛ Bale پاسخ `503` می‌گیرد و cursor را جلو نمی‌برد.
- اگر Planet پیام را ذخیره کند ولی پاسخ از بین برود، Hub دوباره پیام را می‌فرستد؛ `delivery_id` باید idempotency را تضمین کند.

### ۸.۶. امضا و امنیت

هدرها:

```text
Content-Type: application/json
X-Hub-Event: chat.offline
X-Hub-Delivery: <delivery_id>
X-Hub-Timestamp: <unix-seconds>
X-Hub-Signature-256: sha256=<hex hmac>
```

- امضا: `HMAC-SHA256(webhook_secret, timestamp + "." + raw_body)`.
- Planet باید timestamp قدیمی‌تر از چند دقیقه را رد کند.
- Hub redirect را دنبال نمی‌کند (`allow_redirects=False`) و timeout پیش‌فرض ۵ ثانیه است (`HUB_WEBHOOK_TIMEOUT`، حداکثر ۳۰).
- محتوای پیام، secret و URL وب‌هوک در لاگ‌ها ثبت نمی‌شوند.
- URL وب‌هوک از نظر SSRF بررسی می‌شود: فقط HTTPS (به‌صورت پیش‌فرض)، بدون credential/fragment، و رد loopback/private/link-local. هنگام تحویل نیز DNS دوباره resolve و همهٔ آدرس‌ها بررسی می‌شوند.
- `HUB_WEBHOOK_ALLOW_HTTP=1` و `HUB_WEBHOOK_ALLOW_PRIVATE=1` فقط برای توسعه محلی هستند و نباید در production فعال باشند.
- فقط 2xx بعد از commit موفق در Planet موفق است. 4xx/5xx/timeout یعنی شکست.

### ۸.۷. رفتار خطا

| وضعیت | نتیجه |
| --- | --- |
| اپراتور آنلاین | emit فعلی؛ webhook صدا زده نمی‌شود |
| اپراتور آفلاین + webhook 2xx | پیام در Planet ذخیره شده؛ ACK موفق |
| اپراتور آفلاین + webhook 4xx/5xx/timeout | Hub خطا؛ Bale 503 و بدون جلو بردن cursor |
| اپراتور آفلاین + بدون webhook | `store_offline`؛ بدون ACK موفق |
| webhook ذخیره کرد ولی ACK از بین رفت | تکرار؛ Planet با `delivery_id` حذف می‌کند |
| ACK نهایی گم شد | Bale دوباره می‌فرستد؛ at-least-once |

Hub هیچ retry queue داخلی ندارد؛ retry در Bale یا کلاینت است. اگر webhook پاسخ دهد ولی ACK از بین برود، تکرار اجتناب‌ناپذیر است و Planet باید idempotent باشد. برای چت داخلی، ACK خطا شامل `code` برابر `store_offline` یا `webhook_failed` است.

### ۸.۸. محدودیت‌ها و تصمیم‌های باز

- **فقط آفلاین:** اگر اپراتور آنلاین باشد، پیام به webhook نمی‌رود. پس Planet تاریخچهٔ کامل پیام‌های آنلاین را ندارد، مگر UI اپراتور آن‌ها را ثبت کند. اگر Planet باید system of record کامل باشد، webhook باید برای **همهٔ** پیام‌های ورودی صدا زده شود، نه فقط آفلاین.
- **Race:** بین چک کردن اتاق و emit، ممکن است اپراتور قطع شود. این fallback best-effort است. برای دوام سخت، ingest همهٔ پیام‌ها قبل از emit توصیه می‌شود.
- **مشتری چت داخلی آفلاین:** webhook پیام مشتری را ذخیره می‌کند، ولی تحویل پاسخ اپراتور به مشتریِ قطع‌شده هنوز حل نشده؛ Hub تاریخچه ندارد و کلاینت باید از Planet تاریخچه بگیرد.
- **قطعی طولانی Planet:** Bale فقط حدود ۲۴ ساعت/۲٬۰۰۰ آپدیت نگه می‌دارد؛ برای قطعی طولانی‌تر Planet باید HA باشد یا یک spool محلی در `bale_service` اضافه شود.
- **بدون webhook:** رفتار امن `store_offline` است؛ نباید به ACK موفق و دور ریختن پیام برگردیم.

---

<a id="configuration"></a>
## ۹. پیکربندی و اجرا

### ۹.۱. Hub

```bash
# دیتابیس و کاربر MariaDB را یک‌بار جداگانه بسازید؛ database.py آن‌ها را نمی‌سازد.
python database.py   # اجرای مایگریشن‌ها روی دیتابیس موجود
python server.py
```

متغیرهای جدید:

```dotenv
HUB_BALE_SERVICE_TOKEN=replace-with-a-long-random-secret
HUB_ALLOWED_ORIGINS=*
BALE_SERVICE_URL=http://127.0.0.1:8766
HUB_FILE_TIMEOUT=30
 HUB_WEBHOOK_ALLOW_HTTP=0
HUB_WEBHOOK_ALLOW_PRIVATE=0
HUB_WEBHOOK_TIMEOUT=5
# اختیاری: بدون این مقدار کش غیرفعال است
HUB_REDIS_URL=redis://127.0.0.1:6379/0
HUB_CACHE_STRICT=0
HUB_CACHE_TTL_WEBHOOK=10800
HUB_CACHE_TTL_SOURCE=10800
HUB_CACHE_TTL_TOKEN=900
HUB_CACHE_TTL_NEGATIVE=60
```

- `HUB_WEBHOOK_ALLOW_HTTP` و `HUB_WEBHOOK_ALLOW_PRIVATE` فقط برای توسعه محلی‌اند. در production هر دو باید `0` باشند.
- `HUB_ALLOWED_ORIGINS` سیاست Origin سوکت‌ها و CORS مسیر `/api/files` را تعیین می‌کند: `*` هر Origin، مقدار خالی same-origin، یا فهرست کاماجدا شده. Origin احراز هویت نیست؛ اپراتور با توکن پروژه و کلاینت چت با امضای `robot_id + user_id` تأیید می‌شود.
- `HUB_WEBHOOK_TIMEOUT` ثانیه است؛ مقدار پیش‌فرض ۵ و سقف ۳۰.
- `BALE_SERVICE_URL` فقط برای شبکهٔ خصوصی است (پیش‌فرض `http://127.0.0.1:8766`). Hub از این آدرس فایل‌ها را از آداپتر بله می‌گیرد؛ این مسیر نباید از اینترنت قابل دسترس باشد.
- `HUB_FILE_TIMEOUT` مهلت خواندن هر بخش از فایل در Hub (ثانیه، پیش‌فرض ۳۰) است.
- `HUB_REDIS_URL` اگر خالی باشد، کش کاملاً غیرفعال است. با قطعی Redis خواندن‌ها به MySQL fallback می‌کنند.
- `HUB_CACHE_STRICT=1` تغییرات حساس (توکن/webhook/source) را هنگام قطعی Redis با `503` رد می‌کند. پیش‌فرض `0`.
- TTLها ثانیه‌اند؛ مقادیر پیش‌فرض در جدول بخش ۱۰.۳ آمده است.
- migration `004_add_project_webhook.sql` باید قبل از استفاده از webhook روی دیتابیس اجرا شده باشد (`python database.py`).

### ۹.۲. سرویس بله

```bash
python -m bale_service
```

متغیرهای جدید:

```dotenv
HUB_URL=http://127.0.0.1:8765
HUB_BALE_SERVICE_TOKEN=replace-with-a-long-random-secret
BALE_API_BASE=https://tapi.bale.ai
BALE_PUBLIC_BASE_URL=https://support.example.com
BALE_SERVICE_HOST=127.0.0.1
BALE_SERVICE_PORT=8766
BALE_SYNC_INTERVAL=30
BALE_REQUEST_TIMEOUT=15
BALE_HUB_ACK_TIMEOUT=10
BALE_MAX_UPDATE_BYTES=1048576
BALE_WEBHOOK_REQUIRE_HTTPS=1
BALE_DISABLE_WEBHOOK_REGISTRATION=0
BALE_LOG_LEVEL=INFO
# فایل‌ها
BALE_FILE_CACHE_DIR=/var/cache/support-hub/files
BALE_FILE_CACHE_TTL=3600
BALE_FILE_CACHE_MAX_BYTES=536870912
BALE_FILE_MAX_BYTES=20971520
BALE_FILE_DOWNLOAD_TIMEOUT=30
# اختیاری: نگهداری موقت update_idهای ناموفق
HUB_REDIS_URL=redis://127.0.0.1:6379/0
```

- `BALE_PUBLIC_BASE_URL` باید آدرس عمومی HTTPS باشد که بله می‌تواند به آن POST کند. مسیر وب‌هوک `‎/webhooks/bale/{secret}` به آن اضافه می‌شود.
- Reverse Proxy باید این مسیر را به سرویس بله برساند و پورت عمومی ۴۴۳ یا ۸۸ باشد.
- لاگ دسترسی سرویس بله عمداً غیرفعال است تا secret مسیر وب‌هوک در لاگ نیاید. لاگ Reverse Proxy را هم برای این مسیر ماسک کنید.
- اگر `BALE_WEBHOOK_REQUIRE_HTTPS=0` شود، فقط برای توسعه محلی است و بله واقعی آن را نمی‌پذیرد.
- برای توسعه محلی بدون دامنه عمومی، `BALE_DISABLE_WEBHOOK_REGISTRATION=1` تنظیم کنید؛ در این حالت webhook واقعی ثبت نمی‌شود و `BALE_PUBLIC_BASE_URL` لازم نیست.
- اگر `HUB_REDIS_URL` تنظیم شده باشد، پنجرهٔ `update_id`های ناموفق در Redis و با TTL ۲۴ ساعته ذخیره می‌شود؛ در غیر این صورت فقط در حافظهٔ همان پروسه است و با restart پاک می‌شود.
- `BALE_FILE_CACHE_DIR` محل کش موقت فایل‌های دریافتی از بله است؛ `TTL` حداکثر ۱ ساعت (اعتبار لینک `getFile`) توصیه می‌شود و `MAX_BYTES` سقف کل کش و `FILE_MAX_BYTES` سقف هر فایل (پیش‌فرض ۲۰MB، مطابق محدودیت بله) است.
- `BALE_FILE_DOWNLOAD_TIMEOUT` مهلت دانلود یک فایل از بله در ثانیه است.

### ۹.۳. دیتابیس

مایگریشن جدید به `sources` اضافه می‌کند:

- `webhook_secret`: secret یکتای مسیر وب‌هوک.
- `last_update_id`: بالاترین `update_id` پردازش‌شده برای dedup؛ IDهای ناموفق جداگانه در پنجرهٔ موقت (Redis/حافظه) نگه داشته می‌شوند.

این‌ها متادیتا هستند، نه تاریخچه پیام.

---

<a id="redis-cache"></a>
## ۱۰. کش Redis و invalidation

> **وضعیت:** پیاده‌سازی شده است. MySQL منبع حقیقت می‌ماند و Redis فقط کش metadata است. اگر `HUB_REDIS_URL` خالی باشد، هیچ تغییری در مسیرهای فعلی رخ نمی‌دهد و همه‌چیز از MySQL خوانده می‌شود.

### ۱۰.۱. هدف

- کاهش کوئری‌های تکراری MySQL در سه نقطهٔ داغ:
  - `find_source_project` برای هر پیام Socket.IO
  - `authenticate_project` و `find_source_project_with_token` برای هر اتصال
  - `get_project_webhook` وقتی اتاق پروژه خالی است
- جلوگیری از cache stampede هنگام انقضای کلید یا اتصال موجی اپراتورها.
- حفظ invariantها: هیچ پیام، تاریخچه، اپراتور، تخصیص یا وضعیت حضور در Redis ذخیره نمی‌شود.

### ۱۰.۲. چه چیزی کش می‌شود؟

| داده | مقدار کش‌شده | نکته |
| --- | --- | --- |
| webhook پروژه | `webhook_url` و `webhook_secret` | secret باید مثل MySQL محافظت شود |
| source → project | فقط `project_id` و `epoch` | برای bridge و مسیر پیام |
| token → project | فقط `project_id` و `epoch` | خود توکن در value ذخیره نمی‌شود |
| chat source | `project_id`، `client_key` و `epoch` | به‌جای توکن خام، `client_key` کش می‌شود |
| negative cache | «وجود ندارد» | TTL کوتاه برای webhook/source ناموجود |

کش نمی‌شود: متن پیام، فایل، تاریخچه، هویت/تخصیص اپراتور، وضعیت حضور، صف آفلاین.

### ۱۰.۳. کلیدها و TTL

```text
hub:v1:p:{project_id}:epoch
hub:v1:p:{project_id}:{epoch}:webhook
hub:v1:src:{source}:{sha256(robot_id)}
hub:v1:tok:{sha256(token)}
hub:v1:chat:{sha256(robot_id)}
hub:v1:neg:src:{source}:{sha256(robot_id)}
hub:v1:neg:chat:chat:{sha256(robot_id)}
hub:v1:neg:tok:token:{sha256(token)}
```

| کلید | مقدار | TTL پیش‌فرض |
| --- | --- | --- |
| `p:{id}:epoch` | رشتهٔ تصادفی | ۳۰ روز |
| `p:{id}:{epoch}:webhook` | `{"url": "...", "secret": "..."}` | ۳ ساعت |
| `p:{id}:{epoch}:webhook` (نبود webhook) | `{"none": true}` | ۶۰ ثانیه |
| `src:{source}:{hash}` | `{"project_id": 12, "epoch": "..."}` | ۳ ساعت |
| `tok:{hash}` | `{"project_id": 12, "epoch": "..."}` | ۱۵ دقیقه |
| `chat:{hash}` | `{"project_id": 12, "client_key": "...", "epoch": "..."}` | ۳ ساعت |
| `neg:src:...` | `{"none": true}` | ۶۰ ثانیه |
| `neg:tok:...` | `{"none": true}` | ۶۰ ثانیه |

- TTLها با `HUB_CACHE_TTL_WEBHOOK`، `HUB_CACHE_TTL_SOURCE`، `HUB_CACHE_TTL_TOKEN` و `HUB_CACHE_TTL_NEGATIVE` قابل تنظیم‌اند.
- TTL فقط برای پاکسازی خودکار است؛ invalidation واقعی با عوض کردن `epoch` انجام می‌شود.
- TTL توکن عمداً کوتاه‌تر است چون توکن ریشهٔ دسترسی است. ۲–۵ ساعت برای توکن، پنجرهٔ سوءاستفادهٔ طولانی می‌سازد.
- `sha256` روی `robot_id` و توکن فقط برای کوتاه/امن کردن نام کلید است؛ خود `robot_id` و توکن در key خام نمی‌آیند.
- negative keyها epoch ندارند چون برای source ناموجود، `project_id` معلوم نیست؛ هنگام ایجاد/تغییر/حذف source صریحاً حذف می‌شوند.

### ۱۰.۴. مسیر خواندن (cache-aside)

نمونهٔ source lookup:

```text
1. cipher = GET hub:v1:src:{source}:{hash}
2. اگر cipher وجود داشت:
     epoch = GET hub:v1:p:{cipher.project_id}:epoch
     اگر epoch == cipher.epoch: return cipher.project_id
3. project_id = SELECT ... FROM sources ...
4. epoch = GET یا SET NX hub:v1:p:{project_id}:epoch
5. SET hub:v1:src:... {"project_id":..., "epoch":...} EX 3h
```

- token auth: `GET tok:{hash}` → بررسی epoch → در صورت mismatch یا miss، کوئری MySQL و کش دوباره.
- chat source: `GET chat:{hash}` → بررسی epoch → برگرداندن `project_id` و `client_key`.
- webhook: `GET epoch` → `GET p:{id}:{epoch}:webhook` → در صورت miss، MySQL و کش.
- negative cache فقط ۶۰ ثانیه است؛ negative keyهای token/source در تغییرات مربوطه صریحاً حذف می‌شوند و negative key وب‌هوک با epoch جدید بی‌اعتبار می‌شود.
- اگر Redis خطا داد، خواندن از MySQL انجام می‌شود (fail-open) و یک circuit breaker کوتاه ۵ ثانیه‌ای از هجوم درخواست‌های داغ به Redis جلوگیری می‌کند.
- برای هر key یک قفل کوتاه single-flight در حافظهٔ Hub بگذارید تا چند درخواست هم‌زمان چند کوئری تکراری نزنند. Hub تک‌نمونه‌ای است، پس قفل محلی کافی است.

### ۱۰.۵. invalidation

برای هر تغییر در داده‌های کش‌شده:

```text
1. new_epoch = random
2. SET hub:v1:p:{project_id}:epoch new_epoch
3. اگر Redis خطا داد و HUB_CACHE_STRICT=1: پاسخ 503 و بدون تغییر MySQL
4. تغییر را در MySQL انجام بده
5. epoch را دوباره عوض کن (strict=False) تا خواندن‌های هم‌زمان بین مرحله ۲ و ۴ هم بی‌اعتبار شوند
6. کلیدهای negative مربوط به source را حذف کن
7. در صورت نیاز، sessionهای مرتبط را باطل/قطع کن
```

چرا دو بار؟ اگر فقط قبل از نوشتن epoch عوض شود، یک خواندن هم‌زمان می‌تواند دادهٔ قدیمی را زیر epoch جدید کش کند و بعد از نوشتن، تا TTL باقی بماند. چرخش دوم آن نوشتن میانی را بی‌اعتبار می‌کند. اگر فقط بعد از نوشتن عوض شود، بین نوشتن و چرخش، کش قدیمی سرو می‌شود. ترکیب هر دو، پنجره را عملاً می‌بندد.

عوض کردن epoch کافی است؛ کلیدهای قبلی دیگر خوانده نمی‌شوند و با TTL خودشان پاک می‌شوند. نیازی به `SCAN` یا حذف دستی همهٔ کلیدها نیست.

| تغییر | epoch | session |
| --- | --- | --- |
| ثبت اولیه پروژه | لازم نیست | — |
| تعویض توکن پروژه | بله (قبل و بعد) | همهٔ سوکت‌های پروژه قطع شوند |
| تنظیم/تعویض/حذف webhook | بله (قبل و بعد) | لازم نیست |
| ایجاد source جدید | بله (قبل و بعد) | لازم نیست |
| تغییر token/robot source | بله (قبل و بعد) | کلاینت‌های همان source قطع شوند |
| حذف source | بله (قبل و بعد) | کلاینت‌های همان source قطع شوند |
| تغییر نام/domain/description پروژه | بله (فقط قبل) | لازم نیست |
| تغییر user | لازم نیست | — |

- اگر `HUB_REDIS_URL` تنظیم نشده باشد، همه‌چیز فقط از MySQL می‌آید.
- اگر Redis تنظیم شده ولی در دسترس نیست، خواندن‌ها fail-open به MySQL می‌روند. برای تغییرات حساس (تعویض توکن، تغییر webhook، ایجاد/تغییر/حذف source) با `HUB_CACHE_STRICT=1` پاسخ `503` برمی‌گردد و MySQL تغییر نمی‌کند. پیش‌فرض `HUB_CACHE_STRICT=0` است تا قطعی Redis سرویس را کامل نخواباند.
- چرخش دوم بعد از نوشتن همیشه `strict=False` است؛ چون نوشتن انجام شده و 503 دادن آن را برنمی‌گرداند. چرخش اول در حالت strict محافظ اصلی است.
- webhook در حال ارسال قابل لغو نیست؛ تغییر URL از پیام بعدی اثر می‌کند.
- تغییر مستقیم MySQL خارج از API تا TTL کش دیده نمی‌شود؛ مسیر رسمی تغییر، API است.

### ۱۰.۶. session و وب‌سوکت

session سوکت فقط در زمان اتصال احراز هویت می‌شود. برای اینکه تغییرات امنیتی روی سوکت‌های باز اثر کند، دو لایه پیاده‌سازی شده است:

- در session اپراتور `auth_epoch` و در session کلاینت `auth_epoch` + `source_epoch` ذخیره می‌شود.
- Hub شمارنده‌های `project_auth_epoch` و `source_auth_epoch` را در حافظه نگه می‌دارد (Hub تک‌نمونه است).
- در هر رویداد `chat`، مقدار session با مقدار فعلی مقایسه می‌شود؛ در صورت mismatch پیام با `chat_error` و کد `session_expired` رد و سوکت قطع می‌شود.
- علاوه بر این، در تغییرات حساس `disconnect` صریح هم انجام می‌شود:
  - تعویض توکن پروژه: همهٔ سوکت‌های آن پروژه (اپراتور و کلاینت)
  - تغییر robot/token یک source: کلاینت‌های همان source
  - حذف source: کلاینت‌های همان source
- `source_epoch` عمداً از `auth_epoch` جدا است تا تغییر یک source، اپراتورهای همان پروژه را بی‌دلیل قطع نکند.
- source جدید و تغییر webhook نیازی به قطع سوکت ندارند.
- bridge بله با توکن سرویس احراز هویت می‌شود و به source خاصی bind نیست؛ حذف source باعث قطع bridge نمی‌شود، ولی پیام ورودی آن source دیگر مسیر معتبری ندارد.
- بررسی epoch در هر پیام هزینهٔ دیتابیس/Redis ندارد و برای وقتی که `disconnect` شکست بخورد، لایهٔ دفاع دوم است.

### ۱۰.۷. امنیت و عملیات Redis

- Redis باید فقط روی شبکهٔ خصوصی/loopback، با password/ACL و در صورت امکان TLS باشد؛ پورت عمومی نداشته باشد.
- `webhook_secret` و `client_key` در Redis ذخیره می‌شوند؛ سطح دسترسی Redis باید مثل MySQL حساس باشد.
- توکن خام پروژه در value ذخیره نشود؛ فقط `sha256(token)` در key و `project_id` در value.
- URL، secret و متن پیام در لاگ ثبت نشود.
- `maxmemory-policy` مناسب (`allkeys-lru` یا `volatile-lru`) انتخاب شود؛ eviction فقط باعث miss و fallback به MySQL می‌شود.
- persistence (RDB/AOF) اختیاری است؛ Redis منبع حقیقت نیست و با restart/flush باید از MySQL پر شود.
- متریک hit/miss، latency و خطاهای Redis اضافه شود؛ اما دیتابیس باید همچنان مسیر fallback باشد.

---

## ۱۱. محدودیت‌ها و کارهای بعدی

1. **بدون صف پایدار داخلی**: اگر اپراتور آفلاین باشد، پیام از طریق webhook پروژه به Planet می‌رود؛ ولی اگر خود webhook شکست بخورد یا Hub بین دریافت و ACK قطع شود، پیام ورودی ممکن است از دست برود. Hub هیچ retry queue ندارد و retry در Bale/کلاینت است. برای production واقعی یا باید Planet HA باشد یا یک صف کوتاه‌مدت (Redis Streams/Queue) اضافه شود.
2. **بدون تاریخچه در Hub**: طبق تصمیم فعلی، پلنت باید system of record باشد.
3. **بدون ذخیره دائمی فایل**: فایل‌ها از طریق File Resolver و با کش موقت در آداپتور در دسترس‌اند؛ Hub و آداپتور تاریخچهٔ فایل نگه نمی‌دارند. برای نگهداری بلندمدت، Planet باید فایل را در S3/MinIO ذخیره کند.
4. **بدون گروه**: فقط چت خصوصی بله پشتیبانی می‌شود. برای گروه باید sender و conversation از هم جدا شوند.
5. **بدون delivery ACK نهایی**: ارسال به Bale در سرویس لاگ می‌شود، اما اپراتور وضعیت نهایی را نمی‌بیند.
6. **بدون Redis adapter**: Hub تک‌پروسه است و Bale bridge تک‌نمونه.
7. **بدون احراز هویت مشتری بله در Hub**: هویت از webhook و ربات می‌آید.
8. **بدون پاسخ به پیام خاص**: `reply_to_message_id` در قرارداد نیست.
9. **نرخ‌محدودی بله ثابت نیست**: مستندات بله حدود ۲۰ پیام در ثانیه و ۱۲۰ پیام در دقیقه برای هر گفتگو ذکر می‌کند؛ سرویس باید 429 را مدیریت کند.

---

<a id="files"></a>
## ۱۲. فایل و رسانه (File Resolver)

> **وضعیت:** پیاده‌سازی شده برای فایل‌های ورودی بله. Hub هیچ فایل، پیام یا تاریخچه‌ای را دائمی ذخیره نمی‌کند.

### ۱۲.۱. هدف و مرز مسئولیت

- پیام‌های همهٔ کانال‌ها یک قرارداد فایل مشترک دارند؛ UI و Planet برای گرفتن فایل فقط با Hub حرف می‌زنند.
- Hub مرز یکسان است: احراز هویت پروژه، بررسی مالکیت source، و proxy جریان فایل.
- هر provider جزئیات خودش را در آداپتور پنهان می‌کند:
  - بله: `getFile` با توکن بات، لینک ۱ ساعته، دانلود حداکثر ۲۰MB.
  - روبیکا/تلگرام/...: آداپتور آینده با همان قرارداد داخلی.
  - چت داخلی: فایل روی Planet است و `file.url` دارد؛ Hub فایلی ذخیره یا proxy نمی‌کند.
- توکن بات هرگز به Hub یا مرورگر نمی‌رسد؛ فقط آداپتور آن را دارد.
- ذخیره‌سازی دائمی فایل وظیفهٔ Planet است. آداپتور فقط یک کش موقت با TTL دارد.

```text
UI / Planet
    │ GET /api/files?source=bale&robot_id=...&file_id=...   (project token)
    ▼
Support Hub ── GET /internal/files (service token) ──► bale_service ── getFile ──► Bale
    │                                                       │
    │                                                       └─ کش موقت دیسک/حافظه
    └────────────────── stream bytes ◄───────────────────────┘
```

### ۱۲.۲. API یکسان Hub

```http
GET /api/files?source=bale&robot_id=987654&file_id=AgAC...
Authorization: Bearer PROJECT_TOKEN
```

- `source` باید در فهرست sourceهای مجاز باشد.
- Hub با `source + robot_id` بررسی می‌کند که منبع متعلق به همان پروژه است؛ در غیر این صورت `403`/`404`.
- پاسخ موفق: بدنهٔ فایل به‌صورت stream با `Content-Type` و در صورت وجود `Content-Length` و `Content-Disposition`.
- خطاها: `400` ورودی نامعتبر، `404` فایل/منبع ناشناس، `410` فایل منقضی یا غیرقابل دریافت، `413` حجم بیش از حد، `502` خطای آداپتور/provider، `503` آداپتور در دسترس نیست.
- برای `source=chat`، فایل باید از `file.url` روی Planet خوانده شود؛ Hub مسیر فایل ندارد.
- CORS: مسیر `/api/files` درخواست `OPTIONS` و پاسخ‌های GET/خطا را با همان `HUB_ALLOWED_ORIGINS` هدر می‌دهد (`Access-Control-Allow-Origin`، `Authorization`، `Content-Disposition`). با `*` همهٔ دامنه‌ها مجازند؛ با لیست فقط Originهای دقیق. از cookie استفاده نمی‌شود.
- این endpoint با Project Token است؛ ویجت عمومی مشتری نباید توکن پروژه داشته باشد و فایل‌های چت داخلی را از `file.url` Planet می‌خواند.
- دسترسی در سطح **پروژه** است، نه گفتگو؛ چون Hub تاریخچه ندارد و نمی‌تواند مالکیت چت را بررسی کند. جداسازی گفتگویی وظیفهٔ Planet است.

### ۱۲.۳. قرارداد داخلی Hub ↔ آداپتور

```http
GET /internal/files?source=bale&robot_id=987654&file_id=AgAC...
Authorization: Bearer HUB_BALE_SERVICE_TOKEN
```

- این مسیر فقط روی شبکهٔ خصوصی/لوکال باز است و نباید از Reverse Proxy عمومی عبور کند.
- پاسخ موفق: stream فایل؛ خطاها: `400`، `404`، `410`، `413`، `502`.
- آداپتور مسئول `getFile`، دانلود، کش موقت، و نگاشت خطای provider است.
- هر provider جدید فقط باید همین قرارداد را پیاده کند؛ Hub تغییر نمی‌کند.

### ۱۲.۴. قرارداد فایل در پیام هفت‌فیلدی

`file` برای همهٔ providerها شکل ثابت دارد:

```json
{
  "provider": "bale",
  "type": "photo",
  "file_id": "AgAC...",
  "file_name": "photo.jpg",
  "mime_type": "image/jpeg",
  "file_size": 12345,
  "duration": 0,
  "width": 800,
  "height": 600
}
```

- `file_id` شناسهٔ provider است و برای بله **مخصوص همان بات** است.
- URL موقت provider (مثل لینک ۱ ساعتهٔ بله) در پیام قرار نمی‌گیرد؛ UI همیشه از Hub می‌پرسد.
- برای `chat`، `url` پلنت مجاز است و Hub آن را دستکاری نمی‌کند.
- `file_id` از payload پیام گرفته می‌شود و Hub آن را در دیتابیس ذخیره نمی‌کند.

### ۱۲.۵. کش موقت

- کش در آداپتور است، نه Hub؛ کلید `sha256(robot_id + "\0" + file_id)`.
- محل: `BALE_FILE_CACHE_DIR` (پیش‌فرض یک دایرکتوری موقت در سیستم).
- TTL پیش‌فرض ۳۶۰۰ ثانیه است و توصیه می‌شود از اعتبار ۱ ساعتهٔ لینک بله بیشتر نشود.
- سقف هر فایل `BALE_FILE_MAX_BYTES` (پیش‌فرض ۲۰MB) و سقف کل کش `BALE_FILE_CACHE_MAX_BYTES` (پیش‌فرض ۵۱۲MB).
- نوشتن atomic است (فایل موقت + rename) و فایل ناقص سرو نمی‌شود.
- Redis برای متادیتا/URL مناسب است، ولی **بدنهٔ فایل ۲۰ مگابایتی در Redis ذخیره نمی‌شود**؛ حافظه، eviction و تک‌رشته‌ای بودن Redis مناسب بلاب نیست.
- در شروع سرویس و هنگام نوشتن، فایل‌های منقضی و قدیمی برای رعایت سقف پاک می‌شوند.

### ۱۲.۶. نکات بله

- `getFile` لینک را ۱ ساعت معتبر می‌دهد؛ بعد از انقضا باید دوباره `getFile` زده شود.
- دانلود بات از بله حداکثر **۲۰MB** است.
- `file_id` را نمی‌توان بین بات‌ها یا فروشگاه‌ها به اشتراک گذاشت.
- برای ارسال مجدد همان فایل، `file_id` بهترین گزینه است؛ ارسال با URL برای سند فقط GIF/PDF/ZIP و برای صوت OGG تا ۱MB است.
- ارسال فایل جدید با multipart و تبدیل صدای مرورگر به OGG/OPUS در فاز بعد پیاده می‌شود؛ فعلاً خروجی بر اساس `file_id` یا `url` است.
- `sendVoice` برای OGG است؛ صدای ضبط‌شدهٔ مرورگر ممکن است نیاز به تبدیل داشته باشد.
- ارسال بومی استیکر پشتیبانی نمی‌شود؛ مستندات بله `sendSticker` ندارد و تغییر نوع `file_id` مجاز نیست. استیکر فقط دریافت/نمایش می‌شود.

### ۱۲.۷. امنیت و عملیات

- `BALE_SERVICE_URL` فقط آدرس داخلی است؛ endpoint آداپتور نباید عمومی شود.
- احراز هویت آداپتور با `HUB_BALE_SERVICE_TOKEN` و مقایسهٔ constant-time انجام می‌شود.
- UI باید فایل را با `fetch` و هدر `Authorization` بگیرد و به‌صورت blob URL نمایش دهد (یا Planet واسط باشد)؛ `<img src>` مستقیم بدون هدر کار نمی‌کند.
- CORS فایل از همان `HUB_ALLOWED_ORIGINS` پیروی می‌کند؛ preflight بدون احراز هویت پاسخ می‌گیرد و فقط Originهای مجاز هدر CORS دریافت می‌کنند.
- فایل‌ها محتوای مشتری‌اند: مجوز دیسک محدود، لاگ نکردن نام/URL، و پاک‌سازی دوره‌ای الزامی است.
- محدودیت نرخ Hub برای `/api/files` اعمال می‌شود.
- در صورت قطعی آداپتور، Hub خطای `502/503` می‌دهد و UI باید retry/نمایش خطا داشته باشد.
