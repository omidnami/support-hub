# Support Hub

سرویس سبک انتقال مستقیم داده بین پروژه‌ها با REST API و Socket.IO. هر پروژه توکن اختصاصی دارد و پیام‌ها فقط به اتصال‌های فعال همان پروژه ارسال می‌شوند.

## فهرست مستندات

- [راه‌اندازی](#setup)
- [احراز هویت](#authentication)
- [REST API](#rest-api)
- [اتصال سوکت](#socket)
- [کدهای وضعیت](#status-codes)
- [محدوده نسخه فعلی](#scope)

<a id="setup"></a>
## ۱. راه‌اندازی

پیش‌نیازها: Python 3.12 و MariaDB.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

متغیرهای محیطی:

| متغیر | مقدار نمونه | توضیح |
| --- | --- | --- |
| `HUB_DB_HOST` | `127.0.0.1` | آدرس دیتابیس |
| `HUB_DB_PORT` | `3306` | پورت دیتابیس |
| `HUB_DB_NAME` | `support_hub` | نام دیتابیس (ضروری) |
| `HUB_DB_USER` | `hub_user` | کاربر دیتابیس (ضروری) |
| `HUB_DB_PASSWORD` | — | رمز دیتابیس (ضروری) |
| `HUB_HOST` | `127.0.0.1` | آدرس اجرا |
| `HUB_PORT` | `8765` | پورت اجرا |

پس از ایجاد دیتابیس و تنظیم متغیرها:

```bash
python database.py
python server.py
```

دستور اول مایگریشن‌ها را به ترتیب اجرا می‌کند. برای معماری جدید از دیتابیس تازه استفاده کنید؛ این مایگریشن‌ها جداول معماری قدیمی را حذف یا تبدیل نمی‌کنند.

ساختار دیتابیس:

```text
users
  └── projects
        └── sources
```

آدرس پایه نمونه در تمام درخواست‌ها:

```text
http://127.0.0.1:8765
```

در محیط عملیاتی، آدرس سرویس HTTPS خود را جایگزین کنید. تنظیم Reverse Proxy جزو این راهنما نیست.

<a id="authentication"></a>
## ۲. احراز هویت

هویت اتصال اپراتور، **پروژه** است. کلاینت چت با شناسه منبع و شناسه کاربر متصل می‌شود. پس از عضویت اولیه، توکن پروژه را دریافت و فقط در بخش مدیریتی نگهداری کنید.

برای تمام APIها به‌جز عضویت اولیه:

```http
Authorization: Bearer PROJECT_TOKEN
Content-Type: application/json
```

دو نوع توکن وجود دارد:

| توکن | کاربرد |
| --- | --- |
| Project token | احراز هویت REST API و Socket.IO اپراتور |
| Source token | شناسایی و اتصال به سرویس منبع، مانند ربات بله |

توکن پروژه در هدر و توکن منبع در بدنه درخواست ارسال می‌شود. توکن پروژه به مالکیت همان پروژه محدود است؛ API ویرایش کاربر نیز مالک همان پروژه را ویرایش می‌کند.

<a id="rest-api"></a>
## ۳. REST API

| متد | مسیر | کاربرد | احراز هویت |
| --- | --- | --- | --- |
| POST | `/api/register` | عضویت و ایجاد اولین پروژه | ندارد |
| PATCH | `/api/users/me` | ویرایش مالک پروژه | دارد |
| PATCH | `/api/projects/{project_id}` | ویرایش پروژه | دارد |
| POST | `/api/sources` | ثبت منبع | دارد |
| PATCH | `/api/sources/{source_id}` | تغییر توکن منبع | دارد |
| DELETE | `/api/sources/{source_id}` | حذف منبع | دارد |

### ۳.۱. عضویت اولیه

`POST /api/register`

کاربر و اولین پروژه در یک تراکنش ساخته می‌شوند. رمز عبور هش شده و توکن پروژه به‌صورت تصادفی تولید می‌شود.

| فیلد | نوع | الزامی | حداکثر طول |
| --- | --- | --- | --- |
| name | string | بله | 190 |
| email | string | بله | 254 |
| phone | string | بله | 32 |
| password | string | بله | 255 |
| project_name | string | بله | 190 |
| domain | string | خیر | 253 |
| description | string | خیر | 16383 |

نمونه درخواست:

```bash
curl -X POST 'http://127.0.0.1:8765/api/register' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Test User",
    "email": "user@example.ir",
    "phone": "09121234567",
    "password": "example-password",
    "project_name": "My Shop",
    "domain": "example.ir",
    "description": "My first project"
  }'
```

پاسخ موفق — HTTP 201:

```json
{
  "status": true,
  "token": "PROJECT_TOKEN"
}
```

توکن واقعی ۳۲ کاراکتر هگزادسیمال است. شناسه پروژه در پاسخ عضویت وجود ندارد؛ آن را از رویداد `authenticated` سوکت دریافت کنید (بخش ۴).

### ۳.۲. ویرایش اطلاعات کاربر

`PATCH /api/users/me`

اطلاعات مالک پروژه احرازشده تغییر می‌کند. ارسال حداقل یکی از فیلدهای زیر ضروری است:

`name`، `email`، `phone`، `password`

```bash
curl -X PATCH 'http://127.0.0.1:8765/api/users/me' \
  -H 'Authorization: Bearer PROJECT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Updated User","phone":"09120000000"}'
```

پاسخ موفق — HTTP 200:

```json
{"status": true}
```

فقط فیلدهای ارسال‌شده تغییر می‌کنند؛ در صورت ارسال رمز جدید، رمز مجدداً هش می‌شود. در معماری فعلی، توکن هر پروژه اجازه ویرایش مالک مشترک آن پروژه را دارد.

### ۳.۳. ویرایش پروژه

`PATCH /api/projects/{project_id}`

شناسه مسیر باید با پروژه احرازشده توسط توکن مطابقت داشته باشد.

فیلدهای قابل ویرایش: `name`، `domain`، `description`. برای پاک کردن دامنه یا توضیحات می‌توان مقدار `null` ارسال کرد. نام پروژه نمی‌تواند خالی باشد.

```bash
curl -X PATCH 'http://127.0.0.1:8765/api/projects/1' \
  -H 'Authorization: Bearer PROJECT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"name":"New Shop","domain":"new.example.ir","description":null}'
```

پاسخ موفق — HTTP 200:

```json
{"status": true}
```

مالک و توکن پروژه تغییر نمی‌کنند.

### ۳.۴. ثبت منبع

`POST /api/sources`

| فیلد | نوع | توضیح |
| --- | --- | --- |
| name | string | نوع منبع مطابق جدول زیر |
| token | string | توکن ربات یا شناسه چت |
| project_id | integer | شناسه پروژه احرازشده |

همه فیلدها الزامی هستند. مقدار `project_id` باید با توکن پروژه یکسان باشد.

| name | منبع | روش شناسایی در کد فعلی |
| --- | --- | --- |
| bale | بله | getMe |
| telegram | تلگرام | getMe |
| rubika | روبیکا | getMe |
| eitaa | ایتا (ایتایار) | getMe |
| instagram | اینستاگرام | /me با فیلد user_id |
| whatsapp | واتساپ | هنوز پیاده‌سازی نشده (HTTP 501) |
| chat | چت داخلی | robot_id = token، بدون درخواست خارجی |

روش‌های شناسایی پیام‌رسان‌ها در سورس پیاده‌سازی شده‌اند، اما هنوز تست عملیاتی با توکن واقعی انجام نشده است.

نمونه ثبت منبع بله:

```bash
curl -X POST 'http://127.0.0.1:8765/api/sources' \
  -H 'Authorization: Bearer PROJECT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "bale",
    "token": "BOT_TOKEN",
    "project_id": 1
  }'
```

پاسخ موفق — HTTP 201:

```json
{
  "status": true,
  "id": 12,
  "name": "bale",
  "robot_id": "123456",
  "project_id": 1
}
```

- `id`: شناسه رکورد منبع در Support Hub؛ برای ویرایش و حذف استفاده می‌شود.
- `robot_id`: شناسه منبع در پلتفرم، از نوع string.
- `project_id`: شناسه پروژه مالک منبع.

ترکیب `name` و `robot_id` در دیتابیس یکتاست. ثبت مجدد منبع موجود خطای 409 برمی‌گرداند.

**ثبت چت داخلی:**

```json
{
  "name": "chat",
  "token": "my-shop-chat",
  "project_id": 1
}
```

برای چت داخلی مقدار ارسالی بدون ارتباط با API خارجی، هم به‌عنوان توکن و هم به‌عنوان `robot_id` ذخیره می‌شود. در پاسخ نیز همین مقدار به‌عنوان `robot_id` برمی‌گردد. برای منبع ربات، توکن جداگانه در پاسخ وجود ندارد.

### ۳.۵. تغییر توکن منبع

`PATCH /api/sources/{source_id}`

شناسه مسیر، همان `id` دریافتی از ثبت منبع است، نه `robot_id`.

```bash
curl -X PATCH 'http://127.0.0.1:8765/api/sources/12' \
  -H 'Authorization: Bearer PROJECT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"token":"NEW_BOT_TOKEN"}'
```

سرور نوع منبع را از رکورد فعلی می‌خواند و پیش از ذخیره توکن، شناسه ربات را دوباره از پلتفرم دریافت می‌کند. در صورت خطای اعتبارسنجی یا تکراری بودن منبع، رکورد قبلی تغییر نمی‌کند.

پاسخ موفق — HTTP 200:

```json
{
  "status": true,
  "id": 12,
  "name": "bale",
  "robot_id": "987654",
  "project_id": 1
}
```

برای `chat` شناسه جدید برابر توکن جدید است. نوع منبع و پروژه مالک تغییر نمی‌کنند.

### ۳.۶. حذف منبع

`DELETE /api/sources/{source_id}`

فقط منبع متعلق به پروژه احرازشده قابل حذف است. بدنه درخواست لازم نیست.

```bash
curl -X DELETE 'http://127.0.0.1:8765/api/sources/12' \
  -H 'Authorization: Bearer PROJECT_TOKEN'
```

پاسخ موفق — HTTP 200:

```json
{"status": true}
```

حذف فقط روی رکورد منبع در Support Hub انجام می‌شود.

<a id="socket"></a>
## ۴. Socket.IO

- آدرس: `http://127.0.0.1:8765` (در Production آدرس HTTPS سرویس)
- پروتکل: Socket.IO v4، مسیر `/socket.io/`، انتقال WebSocket

### ۴.۱. اتصال اپراتور

```javascript
const operator = io(HUB_URL, {
  transports: ["websocket"],
  auth: { token: PROJECT_TOKEN },
  autoConnect: false
});

operator.on("authenticated", ({ project_id }) => console.log(project_id));
operator.on("connect", () => console.log("online"));
operator.on("disconnect", () => console.log("offline"));
operator.on("connect_error", () => console.log("offline"));
operator.connect();
```

توکن فقط هنگام اتصال بررسی می‌شود: سرور `projects.id` را از روی `projects.token` پیدا می‌کند و اتصال را در Room همان پروژه در RAM نگه می‌دارد. در اتصال مجدد، احراز هویت تکرار می‌شود. توکن در پیام‌ها ارسال نمی‌شود.

### ۴.۲. اتصال کلاینت (بدون توکن پروژه)

پس از ثبت منبع `chat` توسط مدیر پروژه (بخش ۳.۴):

```javascript
const client = io(HUB_URL, {
  transports: ["websocket"],
  auth: {
    role: "client",
    robot_id: "my-shop-chat",
    user_id: "101"
  }
});
```

کلاینت هیچ توکن احراز هویت پروژه یا توکن ربات پیام‌رسانی ارسال نمی‌کند. `auth` نام گزینه اتصال Socket.IO است؛ در این مثال فقط شامل شناسه‌هاست. سرور از روی منبع `chat` و robot_id، پروژه را پیدا می‌کند. شناسه عمومی chat همان robot_id ثبت‌شده است.

**محدودیت فعلی:** مالکیت user_id مستقلاً تأیید نمی‌شود؛ بنابراین این روش به تنهایی برای احراز هویت مشتری در محیط عمومی کافی نیست.

### ۴.۳. قالب مشترک پیام

هر دو سمت با رویداد `chat` پیام را در یک قالب ارسال و دریافت می‌کنند:

```json
{
  "user_id": "101",
  "sender": "client",
  "text": "سلام",
  "file": null,
  "robot_id": "my-shop-chat",
  "timestamp": 1794841200000,
  "source": "chat"
}
```

- `sender`: client یا operator؛ `user_id` در هر دو جهت شناسه مشتری است.
- `file`: null، رشته یا شیء JSON؛ فایل در Hub ذخیره نمی‌شود.
- `timestamp`: Unix timestamp بر حسب میلی‌ثانیه.
- `source + robot_id`: شناسه یکتای منبع برای یافتن `sources.project_id`.

```javascript
// socket می‌تواند operator یا client باشد.
socket.on("chat", (message) => console.log(message));
socket.on("chat_error", (error) => console.error(error.message));
if (socket.connected) socket.emit("chat", message);
```

هر هفت فیلد الزامی‌اند. مسیر پیام با پروژه و هویت اتصال تطبیق داده می‌شود. پیام به اپراتورهای همان پروژه و اتصال‌های همان گفتگو، شامل فرستنده، ارسال می‌شود. ارسال پاسخ به پیام‌رسان‌های غیر chat هنوز پیاده‌سازی نشده است.

### ۴.۴. Ping/Pong و وضعیت اتصال

تنظیمات پیش‌فرض Engine.IO حفظ شده‌اند: سرور هر ۲۵ ثانیه Ping می‌فرستد (ping_interval) و مهلت دریافت Pong از کلاینت ۲۰ ثانیه است (ping_timeout).

**یادآوری برای پیاده‌سازی کلاینت:** در صورت استفاده از WebSocket بومی، به محض دریافت فریم Ping (`2`) پاسخ Pong (`3`) ارسال شود؛ حداکثر مهلت پاسخ ۲۰ ثانیه است. در صورت استفاده از socket.io-client این کار خودکار انجام می‌شود.

وضعیت آنلاین/آفلاین را با رویدادهای `connect`، `disconnect` و `connect_error` و ویژگی `socket.connected` در کلاینت استاندارد مدیریت کنید. هنگام آفلاین بودن ارسال پیام غیرفعال شود.

### ۴.۵. اتصال منابع دیگر

تابع داخلی `emit_source(name, robot_id, "chat", payload)` برای رساندن پیام هفت‌فیلدی از آداپترهای آینده به پروژه است؛ API عمومی نیست. سرویس ارسال و دریافت پیام‌رسان‌ها هنوز پیاده‌سازی نشده است.

سیاست Origin پیش‌فرض Socket.IO فعال است؛ اتصال مرورگر از Origin دیگر هنوز تنظیم نشده است.

<a id="status-codes"></a>
## ۵. کدهای وضعیت HTTP

نمونه پاسخ خطای اعتبارسنجی:

```json
{
  "status": false,
  "message": "Invalid project token."
}
```

| کد | معنی |
| --- | --- |
| 200 | ویرایش یا حذف موفق |
| 201 | عضویت یا ثبت منبع موفق |
| 400 | JSON نامعتبر در عضویت یا ثبت/ویرایش منبع |
| 401 | توکن پروژه وجود ندارد یا نامعتبر است |
| 403 | شناسه پروژه با توکن مطابقت ندارد |
| 404 | منبع متعلق به پروژه یافت نشد |
| 409 | منبع تکراری است |
| 422 | فیلد یا داده نامعتبر؛ یا اعتبارسنجی منبع ناموفق |
| 501 | عملیات واتساپ پیاده‌سازی نشده |
| 502 | API پلتفرم در دسترس نیست |
| 500 | خطای داخلی پیش‌بینی‌نشده |

خطاهای اعتبارسنجی در کد فعلی معمولاً JSON بالا را دارند. خطاهای داخلی کنترل‌نشده ممکن است پاسخ پیش‌فرض aiohttp داشته باشند. JSON نامعتبر در API ویرایش کاربر یا پروژه HTTP 422 برمی‌گرداند.

<a id="scope"></a>
## ۶. محدوده نسخه فعلی

- سه جدول users، projects و sources.
- عضویت اولیه، ویرایش کاربر و پروژه، ثبت/ویرایش توکن/حذف منبع.
- اتصال اپراتور با توکن پروژه؛ اتصال کلاینت با robot_id منبع chat و user_id؛ مسیریابی پیام هفت‌فیلدی به پروژه و گفتگوی مربوطه.
- بدون Redis، صف، ذخیره تاریخچه پیام، اپراتور یا کلاینت در دیتابیس.
- پیام‌های ارسال‌شده هنگام آفلاین بودن مقصد نگهداری نمی‌شوند.
- ثبت منبع به معنی اتصال فعال به ربات نیست؛ Polling، Webhook و ارسال پیام به API پیام‌رسان‌ها در این نسخه وجود ندارند.
- APIهای خواندن فهرست یا جزئیات، ورود کاربر، ساخت پروژه دوم، بازیابی/تعویض توکن پروژه و جلوگیری از عضویت تکراری هنوز پیاده‌سازی نشده‌اند.

این مستند مربوط به پیاده‌سازی کنونی مخزن است، نه تأیید تست عملیاتی یا استقرار روی سرور.
