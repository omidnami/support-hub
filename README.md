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

هویت اتصال، **پروژه** است. پس از عضویت اولیه، توکن پروژه را دریافت و در نرم‌افزار خود نگهداری کنید.

برای تمام APIها به‌جز عضویت اولیه:

```http
Authorization: Bearer PROJECT_TOKEN
Content-Type: application/json
```

دو نوع توکن وجود دارد:

| توکن | کاربرد |
| --- | --- |
| Project token | احراز هویت REST API و Socket.IO |
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
## ۴. اتصال Socket.IO

از کلاینت سازگار با Socket.IO v4 استفاده کنید. اتصال WebSocket خام سازگار نیست.

| مشخصه | مقدار |
| --- | --- |
| آدرس نمونه | `http://127.0.0.1:8765` |
| Path | `/socket.io/` |
| Transport | websocket |
| Auth | `{"token":"PROJECT_TOKEN"}` |
| اتاق داخلی | `project:{project_id}` |

### ۴.۱. اتصال و دریافت شناسه پروژه

نمونه JavaScript با پکیج `socket.io-client`:

```javascript
import { io } from "socket.io-client";

const socket = io("http://127.0.0.1:8765", {
  path: "/socket.io/",
  transports: ["websocket"],
  auth: { token: "PROJECT_TOKEN" },
  autoConnect: false
});

socket.on("authenticated", ({ project_id }) => {
  console.log("Project ID:", project_id);
});

socket.on("connect", () => {
  console.log("Connected");
});

socket.on("connect_error", (error) => {
  console.log("Connection rejected:", error.message);
});

socket.on("disconnect", (reason) => {
  console.log("Disconnected:", reason);
});

socket.on("chat", (data) => {
  console.log("Received:", data);
});

socket.connect();
```

پس از اعتبارسنجی توکن، سرور اتصال را به اتاق پروژه اضافه کرده و رویداد زیر را فقط به همان اتصال ارسال می‌کند:

```json
{"project_id": 1}
```

نام رویداد: `authenticated`. در صورت نامعتبر بودن توکن، اتصال Socket.IO رد می‌شود.

نمونه مرورگر باید با سیاست Origin پیش‌فرض Socket.IO سازگار باشد (معمولاً همان Origin سرویس). در نسخه فعلی CORS برای اتصال از دامنه‌های دیگر تنظیم نشده است. توکن پروژه را در کد عمومی سایت قرار ندهید.

### ۴.۲. ارسال و دریافت داده

پس از اتصال موفق:

```javascript
socket.emit("chat", {
  text: "Hello",
  sender_id: "123456"
});
```

دریافت:

```javascript
socket.on("chat", (data) => {
  console.log(data);
});
```

`chat` صرفاً یک نام رویداد نمونه است. سرور رویدادهای سفارشی و داده همراهشان را به تمام اتصال‌های فعال همان پروژه، شامل فرستنده، منتقل می‌کند. تعیین ساختار داده بر عهده برنامه متصل است.

سرور اتاق مقصد رویداد را از هویت احرازشده تعیین می‌کند، نه از `project_id` داخل داده ارسالی. رویداد `authenticated` برای سرور رزرو شده است.

### ۴.۳. مسیریابی داده‌های منابع (داخل سرور)

تابع داخلی زیر از طریق مقدار بازگشتی `create_app()` قابل دسترسی است:

```python
app, sio, emit_source = create_app()

# پس از راه‌اندازی app و دیتابیس، داخل همان سرویس:
await emit_source("bale", "123456", "chat", {"text": "Hello"})
```

نگاشت منبع به پروژه از جدول `sources` و ترکیب `name + robot_id` انجام می‌شود. داده فقط به اتاق پروژه مرتبط ارسال می‌شود. اگر منبع پیدا نشود، تابع `False` برمی‌گرداند. نتیجه `True` به معنی مسیریابی رویداد است، نه تضمین دریافت توسط مقصد.

این تابع یک REST API عمومی نیست. اتصال واقعی پیام‌رسان‌ها (دریافت پیام و ارسال پاسخ) هنوز ایجاد نشده است.

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
- احراز هویت اتصال با توکن پروژه و Broadcast محدود به پروژه.
- بدون Redis، صف، ذخیره تاریخچه پیام، اپراتور یا کلاینت در دیتابیس.
- پیام‌های ارسال‌شده هنگام آفلاین بودن مقصد نگهداری نمی‌شوند.
- ثبت منبع به معنی اتصال فعال به ربات نیست؛ Polling، Webhook و ارسال پیام به API پیام‌رسان‌ها در این نسخه وجود ندارند.
- APIهای خواندن فهرست یا جزئیات، ورود کاربر، ساخت پروژه دوم، بازیابی/تعویض توکن پروژه و جلوگیری از عضویت تکراری هنوز پیاده‌سازی نشده‌اند.

این مستند مربوط به پیاده‌سازی کنونی مخزن است، نه تأیید تست عملیاتی یا استقرار روی سرور.
