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
   ├── sendPhoto/sendDocument در صورت وجود file
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
HUB_WEBHOOK_ALLOW_HTTP=0
HUB_WEBHOOK_ALLOW_PRIVATE=0
HUB_WEBHOOK_TIMEOUT=5
```

- `HUB_WEBHOOK_ALLOW_HTTP` و `HUB_WEBHOOK_ALLOW_PRIVATE` فقط برای توسعه محلی‌اند. در production هر دو باید `0` باشند.
- `HUB_WEBHOOK_TIMEOUT` ثانیه است؛ مقدار پیش‌فرض ۵ و سقف ۳۰.
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
```

- `BALE_PUBLIC_BASE_URL` باید آدرس عمومی HTTPS باشد که بله می‌تواند به آن POST کند. مسیر وب‌هوک `‎/webhooks/bale/{secret}` به آن اضافه می‌شود.
- Reverse Proxy باید این مسیر را به سرویس بله برساند و پورت عمومی ۴۴۳ یا ۸۸ باشد.
- لاگ دسترسی سرویس بله عمداً غیرفعال است تا secret مسیر وب‌هوک در لاگ نیاید. لاگ Reverse Proxy را هم برای این مسیر ماسک کنید.
- اگر `BALE_WEBHOOK_REQUIRE_HTTPS=0` شود، فقط برای توسعه محلی است و بله واقعی آن را نمی‌پذیرد.
- برای توسعه محلی بدون دامنه عمومی، `BALE_DISABLE_WEBHOOK_REGISTRATION=1` تنظیم کنید؛ در این حالت webhook واقعی ثبت نمی‌شود و `BALE_PUBLIC_BASE_URL` لازم نیست.

### ۹.۳. دیتابیس

مایگریشن جدید به `sources` اضافه می‌کند:

- `webhook_secret`: secret یکتای مسیر وب‌هوک.
- `last_update_id`: آخرین `update_id` پردازش‌شده برای dedup.

این‌ها متادیتا هستند، نه تاریخچه پیام.

---

<a id="redis-cache"></a>
## ۱۰. کش Redis و invalidation (طرح پیشنهادی)

> **وضعیت:** هنوز پیاده‌سازی نشده است. این بخش قرارداد پیاده‌سازی فاز بعد است. MySQL منبع حقیقت می‌ماند و Redis فقط کش است.

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
hub:v1:neg:src:{source}:{sha256(robot_id)}:{epoch}
```

| کلید | مقدار | TTL پیشنهادی |
| --- | --- | --- |
| `p:{id}:epoch` | رشتهٔ تصادفی | طولانی (مثلاً ۳۰ روز، با تمدید) |
| `p:{id}:{epoch}:webhook` | `{"url": "...", "secret": "..."}` | ۳ ساعت |
| `src:{source}:{hash}` | `{"project_id": 12, "epoch": "..."}` | ۳ ساعت |
| `tok:{hash}` | `{"project_id": 12, "epoch": "..."}` | ۱۵ دقیقه |
| `chat:{hash}` | `{"project_id": 12, "client_key": "...", "epoch": "..."}` | ۳ ساعت |
| `neg:src:...` | `"0"` | ۶۰ ثانیه |

- TTL فقط برای پاکسازی خودکار است؛ invalidation واقعی با عوض کردن `epoch` انجام می‌شود.
- TTL توکن عمداً کوتاه‌تر است چون توکن ریشهٔ دسترسی است. ۲–۵ ساعت برای توکن، پنجرهٔ سوءاستفادهٔ طولانی می‌سازد.
- `sha256` روی `robot_id` و توکن فقط برای کوتاه/امن کردن نام کلید است؛ خود `robot_id` و توکن در key خام نمی‌آیند.

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
- negative cache فقط ۶۰ ثانیه؛ هر ایجاد/حذف source یا webhook با epoch جدید آن را بی‌اعتبار می‌کند.
- اگر Redis خطا داد، خواندن از MySQL انجام می‌شود (fail-open). بعد از چند خطای پشت‌سرهم، یک circuit breaker کوتاه برای جلوگیری از latency اضافه شود.
- برای هر key یک قفل کوتاه single-flight در حافظهٔ Hub بگذارید تا چند درخواست هم‌زمان چند کوئری تکراری نزنند. Hub تک‌نمونه‌ای است، پس قفل محلی کافی است.

### ۱۰.۵. invalidation

برای هر تغییر در داده‌های کش‌شده:

```text
1. new_epoch = random
2. SET hub:v1:p:{project_id}:epoch new_epoch
3. اگر Redis خطا داد و تغییر امنیتی است: پاسخ 503 و بدون تغییر MySQL
4. تغییر را در MySQL انجام بده
5. در صورت نیاز، sessionهای مرتبط را باطل/قطع کن
```

عوض کردن epoch کافی است؛ کلیدهای قبلی دیگر خوانده نمی‌شوند و با TTL خودشان پاک می‌شوند. نیازی به `SCAN` یا حذف دستی همهٔ کلیدها نیست.

| تغییر | epoch | session |
| --- | --- | --- |
| ثبت اولیه پروژه | لازم نیست | — |
| تعویض توکن پروژه | بله | همهٔ سوکت‌های پروژه قطع شوند |
| تنظیم/تعویض/حذف webhook | بله | لازم نیست |
| ایجاد source جدید | بله | لازم نیست |
| تغییر token/robot source | بله | کلاینت‌های همان source قطع شوند |
| حذف source | بله | کلاینت‌های همان source قطع شوند |
| تغییر نام/domain/description پروژه | بله | لازم نیست |
| تغییر user | لازم نیست | — |

- اگر `HUB_REDIS_URL` تنظیم نشده باشد، همه‌چیز مثل قبل فقط از MySQL می‌آید.
- اگر Redis تنظیم شده ولی در دسترس نیست، تغییرات حساس (تعویض توکن، تغییر webhook، تغییر/حذف source) بهتر است `503` برگردانند تا کش قدیمی سرو نشود. این رفتار باید با `HUB_CACHE_STRICT` قابل تنظیم باشد.
- webhook در حال ارسال قابل لغو نیست؛ تغییر URL از پیام بعدی اثر می‌کند.
- تغییر مستقیم MySQL خارج از API تا TTL کش دیده نمی‌شود؛ مسیر رسمی تغییر، API است.

### ۱۰.۶. session و وب‌سوکت

وضعیت فعلی: session سوکت فقط بعد از اتصال احراز هویت می‌شود و در پیام‌های بعدی دوباره توکن/امضا بررسی نمی‌شود. بنابراین:

- **کش Redis** با epoch جدید، اتصال‌های جدید را فوراً با دادهٔ جدید احراز هویت می‌کند.
- **سوکت‌های باز قبلی** خودبه‌خود باطل نمی‌شوند.

طرح پیشنهادی برای بستن این شکاف:

- در session سوکت `auth_epoch` ذخیره شود.
- Hub یک `project_auth_epoch` در حافظه نگه دارد (تک‌نمونه).
- در هر رویداد `chat`، `auth_epoch` session با مقدار فعلی پروژه مقایسه شود؛ در صورت mismatch پیام با `chat_error` و کد `session_expired` رد و سوکت قطع شود.
- علاوه بر این، در تغییرات حساس، `disconnect` صریح هم انجام شود:
  - تعویض توکن: همهٔ سوکت‌های آن پروژه (اپراتور و کلاینت)
  - تغییر robot/token یک source: کلاینت‌های همان source
  - حذف source: کلاینت‌های همان source
- source جدید و تغییر webhook نیازی به قطع سوکت ندارند.
- bridge بله با توکن سرویس احراز هویت می‌شود و به source خاصی bind نیست؛ حذف source باعث قطع bridge نمی‌شود، ولی پیام ورودی آن source دیگر مسیر معتبری ندارد.
- بررسی `auth_epoch` در هر پیام هزینهٔ دیتابیس/Redis ندارد و برای وقتی که `disconnect` شکست بخورد، لایهٔ دفاع دوم است.

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
3. **بدون S3**: فایل‌ها فقط متادیتا هستند. برای نمایش پایدار فایل باید دانلود و در S3/MinIO ذخیره شود.
4. **بدون گروه**: فقط چت خصوصی بله پشتیبانی می‌شود. برای گروه باید sender و conversation از هم جدا شوند.
5. **بدون delivery ACK نهایی**: ارسال به Bale در سرویس لاگ می‌شود، اما اپراتور وضعیت نهایی را نمی‌بیند.
6. **بدون Redis adapter**: Hub تک‌پروسه است و Bale bridge تک‌نمونه.
7. **بدون احراز هویت مشتری بله در Hub**: هویت از webhook و ربات می‌آید.
8. **بدون پاسخ به پیام خاص**: `reply_to_message_id` در قرارداد نیست.
9. **نرخ‌محدودی بله ثابت نیست**: سرویس باید 429 را مدیریت کند؛ عدد قطعی از مستندات بله قابل استخراج نیست.
