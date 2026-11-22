# Support Hub

سرویس انتقال پیام بین پروژه‌ها، اپراتورها و کانال‌های پیام‌رسان با REST API و Socket.IO. هر پروژه توکن اختصاصی دارد و پیام‌ها فقط به اتصال‌های فعال همان پروژه ارسال می‌شوند.

سرویس مستقل `bale_service` پیام‌های ربات بله را از webhook دریافت می‌کند، به Hub می‌فرستد و پاسخ اپراتور را با Socket.IO از Hub می‌گیرد و به بله می‌رساند. معماری کامل در [`docs/architecture.md`](docs/architecture.md) آمده است.

## فهرست مستندات

- [معماری کامل](docs/architecture.md)
- [حافظه پروژه برای مدل/ایجنت](AGENTS.md)
- [راه‌اندازی](#setup)
- [احراز هویت](#authentication)
- [REST API](#rest-api)
- [اتصال سوکت](#socket)
- [سرویس بله](#bale-service)
- [پیام‌های فروشگاه آفلاین](#offline-webhook)
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

فایل نمونه تنظیمات را کپی و مقادیر واقعی را جایگزین کنید:

```bash
cp .env.example .env
chmod 600 .env
```

برنامه فایل `.env` را به‌صورت خودکار می‌خواند. متغیرهای محیطی موجود بر مقادیر `.env` اولویت دارند. فایل `.env` در `.gitignore` است و نباید در مخزن قرار بگیرد.

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
| `HUB_TRUST_PROXY` | `0` | فقط پشت Reverse Proxy معتبر `1` شود تا محدودیت نرخ IP را از `X-Forwarded-For` بخواند |
| `HUB_LOG_LEVEL` | `INFO` | سطح لاگ برنامه |
| `HUB_BALE_SERVICE_TOKEN` | — | توکن مشترک سرویس‌به‌سرویس برای اتصال bridge بله؛ برای اجرای سرویس بله الزامی است و باید در Hub و سرویس بله یکسان باشد |
| `HUB_WEBHOOK_ALLOW_HTTP` | `0` | فقط برای توسعه محلی؛ با `1` آدرس HTTP هم برای webhook پروژه پذیرفته می‌شود |
| `HUB_WEBHOOK_ALLOW_PRIVATE` | `0` | فقط برای توسعه محلی؛ با `1` مقصدهای private/loopback هم پذیرفته می‌شوند |
| `HUB_WEBHOOK_TIMEOUT` | `5` | مهلت تحویل webhook آفلاین در ثانیه (حداکثر ۳۰) |
| `HUB_URL` | `http://127.0.0.1:8765` | آدرس Hub برای اتصال سرویس بله |
| `BALE_API_BASE` | `https://tapi.bale.ai` | آدرس API بله |
| `BALE_PUBLIC_BASE_URL` | `https://support.example.com` | آدرس عمومی HTTPS با پورت 443 یا 88؛ مگر اینکه ثبت webhook با `BALE_DISABLE_WEBHOOK_REGISTRATION=1` غیرفعال شود |
| `BALE_SERVICE_HOST` | `127.0.0.1` | آدرس اجرای سرویس بله |
| `BALE_SERVICE_PORT` | `8766` | پورت اجرای سرویس بله |
| `BALE_SYNC_INTERVAL` | `30` | فاصله بررسی منابع بله در ثانیه |
| `BALE_REQUEST_TIMEOUT` | `15` | مهلت درخواست به API بله |
| `BALE_HUB_ACK_TIMEOUT` | `10` | مهلت ACK از Hub |
| `BALE_MAX_UPDATE_BYTES` | `1048576` | حداکثر اندازه بدنه webhook |
| `BALE_WEBHOOK_REQUIRE_HTTPS` | `1` | بررسی HTTPS و پورت 443 یا 88 برای URL عمومی؛ با غیرفعال‌بودن ثبت webhook اعمال نمی‌شود |
| `BALE_DISABLE_WEBHOOK_REGISTRATION` | `0` | با `1` ثبت webhook بله انجام نمی‌شود و `BALE_PUBLIC_BASE_URL` لازم نیست؛ فقط برای توسعه محلی |
| `BALE_LOG_LEVEL` | `INFO` | سطح لاگ سرویس بله |

برای ساخت مقدار `HUB_BALE_SERVICE_TOKEN`:

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

همین مقدار را در `.env` هر دو سرویس قرار دهید. Hub بدون این توکن هم بالا می‌آید، اما bridge بله احراز هویت نمی‌شود و سرویس بله بدون آن اصلاً اجرا نمی‌شود.

### ساخت دیتابیس

`python database.py` فقط مایگریشن‌ها را اجرا می‌کند و دیتابیس یا کاربر نمی‌سازد. یک‌بار با کاربر مدیر MariaDB دیتابیس و کاربر را بسازید (نمونه برای نصب محلی):

```bash
mariadb -u root -p
```

```sql
CREATE DATABASE IF NOT EXISTS support_hub
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'hub_user'@'localhost' IDENTIFIED BY 'STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON support_hub.* TO 'hub_user'@'localhost';
FLUSH PRIVILEGES;
```

مقادیر `HUB_DB_NAME`، `HUB_DB_USER` و `HUB_DB_PASSWORD` در `.env` باید با همین مقادیر یکی باشند. اگر دیتابیس روی سرور دیگری است، `HUB_DB_HOST` را تغییر دهید و دسترسی کاربر را برای همان هاست تنظیم کنید. اگر MariaDB با `skip-name-resolve` اجرا می‌شود، به‌جای `localhost` از `127.0.0.1` برای Host کاربر استفاده کنید. `CREATE USER IF NOT EXISTS` رمز کاربر موجود را تغییر نمی‌دهد؛ در آن صورت رمز را با `ALTER USER` هم‌راستا کنید.

پس از ایجاد دیتابیس و تنظیم متغیرها:

```bash
python database.py
python server.py
```

در ترمینال دوم:

```bash
python -m bale_service
```

دستور اول مایگریشن‌ها را به ترتیب روی دیتابیس موجود اجرا می‌کند. اجرای دوباره آن بی‌خطر است، ولی MariaDB برای جدول‌ها و ستون‌های موجود هشدار `already exists` چاپ می‌کند. برای معماری جدید از دیتابیس تازه استفاده کنید؛ این مایگریشن‌ها جداول معماری قدیمی را حذف یا تبدیل نمی‌کنند. مایگریشن `003` دو ستون `webhook_secret` و `last_update_id` و ایندکس یکتای `uq_sources_webhook_secret` را به `sources` اضافه می‌کند. مایگریشن `004` ستون‌های `webhook_url`، `webhook_secret` و `webhook_updated_at` را برای webhook آفلاین به `projects` اضافه می‌کند.

بررسی سلامت سرویس:

```bash
curl 'http://127.0.0.1:8765/healthz'
```

پاسخ سالم `{"status": true}` است. اگر دیتابیس در دسترس نباشد پاسخ HTTP 503 برمی‌گردد.

اجرای تست‌ها (بدون نیاز به دیتابیس):

```bash
python -m unittest discover -s tests -v
```

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

در محیط عملیاتی، آدرس سرویس HTTPS خود را جایگزین کنید. Reverse Proxy باید مسیر WebSocket را با هدرهای `Upgrade` و `Connection` عبور دهد و `Host` و `X-Forwarded-Proto` اصلی را حفظ کند؛ در غیر این صورت Socket.IO اتصال را به‌دلیل Origin رد می‌کند. تنظیم جزئی Reverse Proxy جزو این راهنما نیست.

این سرویس اتاق‌های Socket.IO را در RAM نگه می‌دارد و از Redis استفاده نمی‌کند؛ بنابراین باید فقط یک پروسه از برنامه اجرا شود. اجرای چند worker باعث می‌شود اتصال‌های هم‌پروژه یکدیگر را نبینند.

<a id="authentication"></a>
## ۲. احراز هویت

هویت اتصال اپراتور، **پروژه** است. کلاینت چت با هویت امضاشده کاربر متصل می‌شود. پس از عضویت اولیه، توکن پروژه را دریافت و فقط در بخش مدیریتی/بک‌اند نگهداری کنید.

برای تمام APIها به‌جز عضویت اولیه و بررسی سلامت:

```http
Authorization: Bearer PROJECT_TOKEN
Content-Type: application/json
```

چهار نوع اعتبار وجود دارد:

| اعتبار | کاربرد | محل نگهداری |
| --- | --- | --- |
| Project token | احراز هویت REST API و Socket.IO اپراتور | فقط بک‌اند مدیریتی |
| Source token | ثبت/به‌روزرسانی منبع و اتصال به API پیام‌رسان | فقط بک‌اند سرویس |
| Client key | امضای هویت کلاینت چت توسط بک‌اند پروژه | فقط بک‌اند پروژه |
| Bale bridge token | احراز هویت سرویس بله در Socket.IO | فقط Hub و سرویس بله |

- `HUB_BALE_SERVICE_TOKEN` را با یک مقدار تصادفی طولانی پر کنید. این توکن اجازه ارسال پیام به اتاق bridge بله را می‌دهد و نباید در مرورگر یا مخزن عمومی قرار بگیرد.
- سرویس بله فقط یک اتصال فعال به ازای provider نگه می‌دارد؛ اتصال bridge دوم رد می‌شود تا پاسخ‌ها دوبار به بله نرسند.

- توکن پروژه در هدر و توکن منبع در بدنه درخواست ارسال می‌شود.
- توکن پروژه ۳۲ کاراکتر هگزادسیمال و ریشه دسترسی پروژه است؛ آن را در مرورگر قرار ندهید.
- `client_key` از توکن پروژه مشتق می‌شود و ۶۴ کاراکتر هگزادسیمال است. از آن فقط برای امضای هویت کلاینت استفاده کنید و هرگز آن را به مرورگر نفرستید.
- با تعویض توکن پروژه، `client_key` و همه امضاهای قبلی کلاینت باطل می‌شوند.
- توکن پروژه به مالکیت همان پروژه محدود است؛ API ویرایش کاربر نیز مالک همان پروژه را ویرایش می‌کند.

### امضای هویت کلاینت

بک‌اند پروژه برای هر مشتری یک مقدار `expires` (Unix timestamp ثانیه) و امضای HMAC-SHA256 زیر می‌سازد:

```text
message = robot_id + "\n" + user_id + "\n" + expires
signature = HMAC-SHA256(bytes.fromhex(client_key), message)
```

نمونه پایتون:

```python
import hashlib
import hmac
import time

expires = int(time.time()) + 300
message = f"{robot_id}\n{user_id}\n{expires}".encode()
signature = hmac.new(bytes.fromhex(client_key), message, hashlib.sha256).hexdigest()
```

سرور امضا و انقضا را بررسی می‌کند. حداکثر عمر مجاز توکن ۳۶۰۰ ثانیه (به‌همراه ۶۰ ثانیه اختلاف ساعت) است؛ برای محیط عملیاتی مقدار کوتاه‌تر توصیه می‌شود.

<a id="rest-api"></a>
## ۳. REST API

| متد | مسیر | کاربرد | احراز هویت |
| --- | --- | --- | --- |
| POST | `/api/register` | عضویت و ایجاد اولین پروژه | ندارد |
| GET | `/healthz` | بررسی سلامت سرویس و دیتابیس | ندارد |
| PATCH | `/api/users/me` | ویرایش مالک پروژه | دارد |
| PATCH | `/api/projects/{project_id}` | ویرایش پروژه | دارد |
| POST | `/api/projects/{project_id}/token` | تعویض توکن پروژه | دارد |
| POST | `/api/projects/{project_id}/webhook` | ثبت یا تعویض webhook پروژه | دارد |
| DELETE | `/api/projects/{project_id}/webhook` | حذف webhook پروژه | دارد |
| GET | `/api/projects/{project_id}/client-key` | دریافت کلید امضای کلاینت | دارد |
| POST | `/api/sources` | ثبت منبع | دارد |
| PATCH | `/api/sources/{source_id}` | تغییر توکن منبع | دارد |
| DELETE | `/api/sources/{source_id}` | حذف منبع | دارد |

### ۳.۱. عضویت اولیه

`POST /api/register`

کاربر و اولین پروژه در یک تراکنش ساخته می‌شوند. رمز عبور هش شده و توکن پروژه و `client_key` به‌صورت تصادفی/مشتق تولید می‌شوند.

| فیلد | نوع | الزامی | طول |
| --- | --- | --- | --- |
| name | string | بله | حداکثر 190 |
| email | string | بله | حداکثر 254 |
| phone | string | بله | حداکثر 32 |
| password | string | بله | حداقل 8، حداکثر 255 |
| project_name | string | بله | حداکثر 190 |
| domain | string | خیر | حداکثر 253 |
| description | string | خیر | حداکثر 16383 |
| webhook_url | string | خیر | حداکثر 2048، HTTPS |

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
  "token": "PROJECT_TOKEN",
  "project_id": 1,
  "client_key": "CLIENT_KEY",
  "webhook_url": "https://planet.example.ir/hooks/hub",
  "webhook_secret": "WEBHOOK_SECRET"
}
```

`webhook_url` و `webhook_secret` فقط وقتی برمی‌گردند که `webhook_url` در درخواست ارسال شده باشد. secret را Hub می‌سازد و **فقط همین یک‌بار** برمی‌گرداند؛ باید در Planet ذخیره شود. `webhook_url` باید HTTPS، بدون credential و fragment، و بدون مقصد private/loopback باشد؛ در غیر این صورت HTTP 422 برمی‌گردد. اگر webhook ارسال نشود، پیام‌های ورودیِ فروشگاه آفلاین با خطای `store_offline` رد می‌شوند.

توکن پروژه ۳۲ کاراکتر هگزادسیمال و `client_key` ۶۴ کاراکتر هگزادسیمال است. `project_id` و `client_key` را در بک‌اند نگهداری کنید. جلوگیری از عضویت تکراری با ایمیل یکسان هنوز پیاده‌سازی نشده است.

این مسیر و سایر مسیرهای حساس نرخ‌محدود هستند؛ در صورت عبور از حد مجاز HTTP 429 برمی‌گردد.

### ۳.۲. ویرایش اطلاعات کاربر

`PATCH /api/users/me`

اطلاعات مالک پروژه احرازشده تغییر می‌کند. ارسال حداقل یکی از فیلدهای زیر ضروری است:

`name`، `email`، `phone`، `password`

رمز جدید باید حداقل ۸ و حداکثر ۲۵۵ کاراکتر باشد.

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

روش‌های شناسایی پیام‌رسان‌ها در سورس پیاده‌سازی شده‌اند. برای `bale`، پس از ثبت منبع، سرویس بله به‌صورت خودکار webhook را روی همان ربات تنظیم می‌کند. تست عملیاتی با توکن واقعی بله باید در محیط استقرار انجام شود.

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

توکن منبع در دیتابیس ذخیره می‌شود؛ دسترسی مستقیم به دیتابیس را محدود کنید.

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

اگر توکن یا `robot_id` بله واقعاً تغییر کند، `webhook_secret` و `last_update_id` همان منبع پاک می‌شوند؛ چون هر دو به ربات قبلی تعلق دارند. سرویس بله در همگام‌سازی بعدی secret جدید می‌سازد و webhook را دوباره ثبت می‌کند. تا آن زمان webhook قبلی معتبر نیست و ممکن است چند پیام ورودی از دست برود.

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

حذف فقط روی رکورد منبع در Support Hub انجام می‌شود. سرویس بله در همگام‌سازی بعدی (حداکثر `BALE_SYNC_INTERVAL` ثانیه) webhook همان ربات را از بله حذف می‌کند.

### ۳.۷. تعویض توکن پروژه

`POST /api/projects/{project_id}/token`

شناسه مسیر باید با پروژه احرازشده مطابقت داشته باشد. بدنه درخواست لازم نیست.

```bash
curl -X POST 'http://127.0.0.1:8765/api/projects/1/token' \
  -H 'Authorization: Bearer PROJECT_TOKEN'
```

پاسخ موفق — HTTP 200:

```json
{
  "status": true,
  "token": "NEW_PROJECT_TOKEN",
  "client_key": "NEW_CLIENT_KEY"
}
```

توکن قبلی بلافاصله برای درخواست‌های جدید معتبر نیست. اتصال‌های اپراتور و کلاینتی که از قبل برقرار شده‌اند تا زمان قطع شدن باقی می‌مانند، اما اتصال مجدد نیاز به توکن/امضای جدید دارد. با تعویض توکن، `client_key` جدید می‌شود و امضاهای قبلی برای اتصال‌های جدید پذیرفته نمی‌شوند. پاسخ این درخواست را ذخیره کنید؛ توکن قبلی دیگر قابل بازیابی نیست.

### ۳.۸. دریافت کلید امضای کلاینت

`GET /api/projects/{project_id}/client-key`

```bash
curl 'http://127.0.0.1:8765/api/projects/1/client-key' \
  -H 'Authorization: Bearer PROJECT_TOKEN'
```

پاسخ موفق — HTTP 200:

```json
{
  "status": true,
  "client_key": "CLIENT_KEY"
}
```

این مقدار از توکن فعلی پروژه مشتق می‌شود. فقط در بک‌اند پروژه استفاده شود و به مرورگر ارسال نشود. برای دریافت کلید جدید، توکن پروژه را از مسیر ۳.۷ تعویض کنید.

### ۳.۹. ثبت و حذف webhook پروژه

`POST /api/projects/{project_id}/webhook`

webhook پروژه (فروشگاه) را ثبت یا جایگزین می‌کند و **secret جدید** می‌سازد. secret قبلی بلافاصله باطل می‌شود.

```bash
curl -X POST 'http://127.0.0.1:8765/api/projects/1/webhook' \
  -H 'Authorization: Bearer PROJECT_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"webhook_url":"https://planet.example.ir/hooks/hub"}'
```

پاسخ موفق — HTTP 200 با `Cache-Control: no-store`:

```json
{
  "status": true,
  "webhook_url": "https://planet.example.ir/hooks/hub",
  "webhook_secret": "WEBHOOK_SECRET"
}
```

`DELETE /api/projects/{project_id}/webhook` آدرس و secret را پاک می‌کند. از این پس اگر اپراتوری آنلاین نباشد، پیام‌های ورودی با خطای `store_offline` رد می‌شوند. هر دو مسیر نرخ‌محدود هستند و شناسه مسیر باید با پروژه توکن مطابقت داشته باشد.

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

### ۴.۲. اتصال کلاینت (هویت امضاشده)

پس از ثبت منبع `chat` توسط مدیر پروژه (بخش ۳.۴)، بک‌اند پروژه با `client_key` یک امضای کوتاه‌عمر برای هر مشتری می‌سازد:

```javascript
const client = io(HUB_URL, {
  transports: ["websocket"],
  auth: {
    role: "client",
    robot_id: "my-shop-chat",
    user_id: "101",
    expires: EXPIRES_SECONDS,
    signature: SIGNATURE_HEX
  }
});
```

کلاینت هیچ توکن پروژه یا توکن ربات پیام‌رسانی ارسال نمی‌کند؛ فقط هویت امضاشده را می‌فرستد. سرور از روی منبع `chat` و `robot_id` پروژه را پیدا می‌کند، `client_key` را از توکن پروژه مشتق می‌کند و امضا و انقضا را بررسی می‌کند. در صورت عدم تطابق یا انقضا، اتصال رد می‌شود.

`user_id` در امضا قفل می‌شود و کلاینت نمی‌تواند وارد گفتگوی کاربر دیگری شود. امضای صادرشده تا زمان انقضا نقش Bearer دارد؛ بنابراین عمر آن را کوتاه و `client_key` را فقط در بک‌اند نگه دارید.

رویداد `authenticated` برای کلاینت شامل `role`، `robot_id` و `user_id` است و `project_id` را برنمی‌گرداند.

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

محدودیت‌ها:

- `text` حداکثر ۱۰٬۰۰۰ کاراکتر.
- `file` حداکثر ۶۵٬۵۳۶ بایت پس از سریال‌سازی (برای رشته یا شیء JSON).
- هر اتصال حداکثر ۳۰ پیام در هر ۱۰ ثانیه می‌تواند ارسال کند؛ در غیر این صورت `chat_error` با پیام «Too many messages» برمی‌گردد.

```javascript
// socket می‌تواند operator یا client باشد.
socket.on("chat", (message) => console.log(message));
socket.on("chat_error", (error) => console.error(error.message));
if (socket.connected) socket.emit("chat", message);
```

هر هفت فیلد الزامی‌اند. مسیر پیام با پروژه و هویت اتصال تطبیق داده می‌شود. پیام به اپراتورهای همان پروژه و اتصال‌های همان گفتگو، شامل فرستنده، ارسال می‌شود. اگر هیچ اپراتوری در اتاق پروژه آنلاین نباشد و webhook پروژه تنظیم شده باشد، پیام به‌جای emit به webhook امضاشدهٔ Planet می‌رود و فقط با پاسخ 2xx ACK موفق می‌گیرد. ارسال پاسخ به پیام‌رسان‌های غیر chat هنوز پیاده‌سازی نشده است.

### ۴.۴. Ping/Pong و وضعیت اتصال

تنظیمات پیش‌فرض Engine.IO حفظ شده‌اند: سرور هر ۲۵ ثانیه Ping می‌فرستد (ping_interval) و مهلت دریافت Pong از کلاینت ۲۰ ثانیه است (ping_timeout).

**یادآوری برای پیاده‌سازی کلاینت:** در صورت استفاده از WebSocket بومی، به محض دریافت فریم Ping (`2`) پاسخ Pong (`3`) ارسال شود؛ حداکثر مهلت پاسخ ۲۰ ثانیه است. در صورت استفاده از socket.io-client این کار خودکار انجام می‌شود.

وضعیت آنلاین/آفلاین را با رویدادهای `connect`، `disconnect` و `connect_error` و ویژگی `socket.connected` در کلاینت استاندارد مدیریت کنید. هنگام آفلاین بودن ارسال پیام غیرفعال شود.

### ۴.۵. اتصال bridge بله

سرویس بله با یک اتصال سرویس‌به‌سرویس به Hub وصل می‌شود، نه با توکن پروژه و نه با هویت مشتری:

```javascript
const bridge = io(HUB_URL, {
  transports: ["websocket"],
  auth: {
    role: "bridge",
    provider: "bale",
    token: BALE_SERVICE_TOKEN
  }
});
```

- سرویس بله در اتاق `bridge:bale` قرار می‌گیرد.
- پیام ورودی مشتری را با `sender: "client"` و `source: "bale"` می‌فرستد؛ Hub آن را به اتاق پروژه می‌رساند یا در صورت خالی بودن اتاق به webhook پروژه می‌دهد.
- سرویس بله می‌تواند یک آرگومان دوم اختیاری با metadata انتقال بفرستد: `{"update_id": 12345, "chat_id": "501"}`. این metadata به هفت فیلد پیام اضافه نمی‌شود و فقط برای `delivery_id` و `conversation.chat_id` استفاده می‌شود.
- پاسخ اپراتور با `sender: "operator"` و `source: "bale"` از Hub به همان اتاق bridge می‌رسد و سرویس بله آن را با `sendMessage` به بله می‌فرستد.
- اگر bridge آنلاین نباشد، Hub پاسخ اپراتور را با `chat_error` رد می‌کند.
- فقط یک اتصال bridge فعال برای `bale` پذیرفته می‌شود.

پیام‌های منابع دیگر در آینده با همین الگوی bridge اضافه می‌شوند. تابع داخلی `emit_source` نیز برای آداپترهای درون‌پروسه‌ای باقی مانده است و API عمومی نیست.

سیاست Origin پیش‌فرض Socket.IO فعال است و اتصال از Origin دیگر مجاز نیست. برای استقرار، Reverse Proxy باید `Host` و `X-Forwarded-Proto` اصلی را حفظ کند.

<a id="bale-service"></a>
## ۵. سرویس بله

سرویس `bale_service` مستقل از Hub اجرا می‌شود و سه کار انجام می‌دهد:

1. منابع `bale` را از دیتابیس می‌خواند و برای هر ربات یک webhook امن ثبت می‌کند.
2. آپدیت‌های بله را از webhook دریافت می‌کند و با Socket.IO به Hub می‌فرستد.
3. پاسخ اپراتور را از Hub می‌گیرد و با API بله به مشتری می‌فرستد.

### ۵.۱. پیش‌نیازها

- مایگریشن‌های `003_add_bale_webhook_metadata.sql` و `004_add_project_webhook.sql` (برای Hub) اجرا شده باشند.
- `HUB_BALE_SERVICE_TOKEN` در Hub و سرویس بله یکسان باشد.
- `BALE_PUBLIC_BASE_URL` یک دامنه HTTPS با پورت ۴۴۳ یا ۸۸ باشد؛ اگر `BALE_DISABLE_WEBHOOK_REGISTRATION=1` باشد این مورد لازم نیست.
- Reverse Proxy مسیر `‎/webhooks/bale/<secret>` را به سرویس بله برساند. بقیه مسیرها لازم نیستند.
- لاگ دسترسی خود سرویس بله غیرفعال است تا secret داخل URL در لاگ نیاید. اگر Reverse Proxy لاگ دسترسی دارد، مسیر webhook را حذف یا ماسک کنید؛ این secret تنها لایه احراز هویت webhook است.

```bash
python -m bale_service
```

بررسی سلامت:

```bash
curl 'http://127.0.0.1:8766/healthz'
```

پاسخ سالم شامل `database: true`، `hub_connected: true` و تعداد منابع است. اگر دیتابیس در دسترس نباشد یا bridge بله هنوز متصل/احراز هویت نشده باشد، HTTP 503 برمی‌گردد.

### ۵.۲. ثبت webhook

- سرویس بله هر ۳۰ ثانیه (قابل تنظیم با `BALE_SYNC_INTERVAL`) منابع `bale` را همگام می‌کند.
- برای هر منبع، یک `webhook_secret` تصادفی در `sources` ساخته می‌شود.
- URL نهایی: `BALE_PUBLIC_BASE_URL + /webhooks/bale/ + webhook_secret`
- سرویس با `setWebhook` آن را روی بله ثبت می‌کند. بله secret یا امضای درخواست ندارد؛ بنابراین secret داخل URL تنها لایه احراز هویت webhook است. این secret نباید در لاگ دسترسی سرویس یا Reverse Proxy ثبت شود.
- هنگام تغییر توکن یا شناسه ربات، `webhook_secret` و `last_update_id` قبلی باطل می‌شوند و در همگام‌سازی بعدی webhook جدید ثبت می‌شود.
- هنگام حذف منبع، سرویس در همگام‌سازی بعدی webhook همان ربات را حذف می‌کند.
- اگر ثبت webhook در یک دوره شکست بخورد، در دوره بعدی دوباره تلاش می‌شود.

### ۵.۳. جریان ورودی

```text
Bale → POST /webhooks/bale/<secret>
     → پیدا کردن source و project
     → dedup با update_id
     → تبدیل به پیام هفت‌فیلدی
     → Socket.IO bridge → Hub
                         ├─ اپراتور آنلاین: اتاق پروژه
                         └─ اپراتور آفلاین: webhook پروژه در Planet
```

- `update_id` در `sources.last_update_id` ذخیره می‌شود و فقط پس از ACK موفق Hub جلو می‌رود. اگر ACK از بین برود، ممکن است پیام یک‌بار تکراری به اپراتور برسد؛ پلنت باید این حالت را تحمل کند.
- bridge بله همراه پیام، metadata انتقال (`update_id` و `chat_id`) را در آرگومان دوم می‌فرستد؛ این‌ها به هفت فیلد پیام اضافه نمی‌شوند.
- اگر هیچ اپراتوری در اتاق پروژه نباشد، Hub پیام را با webhook امضاشده به Planet می‌فرستد و فقط پس از پاسخ 2xx به bridge ACK می‌دهد. اگر webhook تنظیم نشده یا شکست بخورد، پاسخ webhook بله 503 است و `last_update_id` جلو نمی‌رود.
- فقط چت خصوصی پذیرفته می‌شود. پیام گروه/کانال و پیام ربات‌ها نادیده گرفته می‌شود.
- متن از `text` یا `caption` گرفته می‌شود.
- برای فایل‌ها فقط متادیتا (نوع، `file_id`، نام، MIME و اندازه) در `file` قرار می‌گیرد. فایل در Hub یا سرویس بله ذخیره نمی‌شود.
- اگر Hub در دسترس نباشد، پاسخ webhook موقتاً 503 است تا بله در صورت پشتیبانی دوباره تلاش کند.

### ۵.۴. جریان خروجی

```text
اپراتور → Hub → اتاق bridge:bale → سرویس بله
        → sendMessage یا sendPhoto/sendDocument → مشتری بله
```

- `user_id` همان `message.chat.id` است و به `chat_id` بله نگاشت می‌شود.
- متن بلندتر از ۴۰۹۶ کاراکتر به چند پیام تقسیم می‌شود. کپشن فایل حداکثر ۱۰۲۴ کاراکتر است و ادامه آن به‌صورت پیام متنی جداگانه ارسال می‌شود.
- اگر توکن ربات بین دو همگام‌سازی عوض شده باشد، سرویس با خطای 401 توکن تازه را از دیتابیس می‌خواند و ارسال را یک‌بار دیگر امتحان می‌کند.
- پیام‌های هر ربات در صف جداگانه و به ترتیب پردازش می‌شوند.
- خطای 429 با یک تلاش مجدد کوتاه مدیریت می‌شود؛ خطاهای دائمی فقط در لاگ ثبت می‌شوند.
- وضعیت تحویل نهایی به اپراتور برنمی‌گردد؛ ACK فعلی فقط تا دریافت Hub است.

### ۵.۵. محدودیت‌های فعلی بله

- فقط چت خصوصی؛ گروه و کانال پشتیبانی نمی‌شوند.
- فایل‌ها در S3/MinIO ذخیره نمی‌شوند و لینک پایدار ندارند.
- `reply_to_message_id` در قرارداد نیست.
- پیام‌های آفلاین در Hub صف نمی‌شوند؛ اگر اپراتور آنلاین نباشد، پیام به webhook پروژه در Planet فرستاده می‌شود و بدون webhook یا بدون پاسخ 2xx، پیام ACK موفق نمی‌گیرد.
- فقط یک نمونه از سرویس بله و یک نمونه از Hub باید اجرا شود.
- برای توسعه محلی بدون دامنه عمومی، `BALE_DISABLE_WEBHOOK_REGISTRATION=1` را تنظیم کنید؛ در این حالت `BALE_PUBLIC_BASE_URL` لازم نیست و `BALE_WEBHOOK_REQUIRE_HTTPS` اعمال نمی‌شود، اما webhook بله واقعی هم کار نمی‌کند. فقط مسیر سرویس قابل تست دستی است.

<a id="offline-webhook"></a>
## ۶. پیام‌های فروشگاه آفلاین و webhook پلنت

این رفتار **پیاده‌سازی شده** است. Hub همچنان هیچ پیام یا صفی ذخیره نمی‌کند؛ فقط وقتی اتاق پروژه خالی است، پیام را به webhook خود پروژه در Planet تحویل می‌دهد و تنها با پاسخ 2xx موفق می‌شود.

### ۶.۱. جریان

```text
پیام از هر کانال (بله، چت داخلی، آداپتور بعدی)
   → Hub پیام را اعتبارسنجی و project_id را از source/session استخراج می‌کند
   → آیا اتاق project:{project_id} اپراتور آنلاین دارد؟
        ├─ بله: emit فعلی به اپراتورها
        └─ خیر: POST امضاشده به webhook پروژه در Planet
                 ├─ 2xx: ACK موفق به فرستنده/bridge
                 └─ خطا/timeout/بدون webhook: خطا؛ Bale پاسخ 503 و cursor جلو نمی‌رود
```

- webhook در سطح پروژه/فروشگاه است، نه یک منبع؛ چون باید برای بله، چت داخلی، روبیکا و کانال‌های بعدی یکسان کار کند.
- `webhook_url` و `webhook_secret` در جدول `projects` ذخیره می‌شوند. ثبت اولیه یا `POST /api/projects/{id}/webhook` آن‌ها را می‌سازد/جایگزین می‌کند.
- Hub وجود اپراتور را فقط از روی اتصال‌های نقش `operator` در اتاق پروژه تشخیص می‌دهد. هویت، تخصیص یا تعداد اپراتورها را نمی‌داند.
- اگر اپراتوری آنلاین باشد، webhook صدا زده نمی‌شود.
- فراخوانی webhook با `allow_redirects=False` و timeout کوتاه انجام می‌شود. فقط 2xx یعنی تحویل موفق.

### ۶.۲. chat_id برای همه کانال‌ها

`chat_id` شناسهٔ گفتگو در خود کانال است و به‌صورت metadata در webhook می‌آید، نه به‌عنوان فیلد هشتم پیام:

| کانال | chat_id |
| --- | --- |
| بله | `message.chat.id`؛ سرویس بله آن را در metadata می‌فرستد |
| چت داخلی | `user_id` امضاشده |
| روبیکا/تلگرام/واتساپ/اینستاگرام | chat/thread id همان پلتفرم |

کلید یکتای گفتگو:

```text
conversation_key = (project_id, source, robot_id, chat_id)
```

برای چت خصوصی معمولاً `chat_id == user_id` است؛ برای گروه در آینده این دو جدا می‌شوند.

### ۶.۳. قرارداد و امضا

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

`message` دقیقاً همان هفت فیلد است. `project_id` و `conversation` metadata انتقال‌اند. `sender` همیشه `client` است؛ پاسخ اپراتور به webhook نمی‌رود.

هدرها:

```text
Content-Type: application/json
X-Hub-Event: chat.offline
X-Hub-Delivery: <delivery_id>
X-Hub-Timestamp: <unix-seconds>
X-Hub-Signature-256: sha256=<hex hmac>
```

امضا با `HMAC-SHA256(webhook_secret, timestamp + "." + raw_body)` محاسبه می‌شود. Planet باید timestamp قدیمی را رد کند و `delivery_id` را برای idempotency یکتا نگه دارد.

- برای بله `delivery_id` از `source:robot_id:update_id` ساخته می‌شود.
- برای کانال بدون شناسه پیام، hash پایدار از source/robot_id/chat_id/timestamp/message ساخته می‌شود.
- اگر Planet ذخیره کند ولی ACK از بین برود، تکرار اجتناب‌ناپذیر است؛ Planet باید با `delivery_id` تکرارها را حذف کند.

### ۶.۴. رفتار خطا

| وضعیت | نتیجه |
| --- | --- |
| اپراتور آنلاین | emit فعلی؛ webhook صدا زده نمی‌شود |
| اپراتور آفلاین + webhook 2xx | پیام در Planet ذخیره شده؛ ACK موفق |
| اپراتور آفلاین + webhook 4xx/5xx/timeout | ACK ناموفق؛ Bale پاسخ 503 و بدون جلو بردن cursor |
| اپراتور آفلاین + بدون webhook | خطای `store_offline`؛ بدون ACK موفق |
| webhook ذخیره کرد ولی ACK از بین رفت | تکرار؛ Planet با `delivery_id` حذف می‌کند |

برای چت داخلی، خطا به‌صورت `chat_error` و ACK با `code` برابر `store_offline` یا `webhook_failed` برمی‌گردد.

### ۶.۵. پیکربندی و امنیت

```dotenv
HUB_WEBHOOK_ALLOW_HTTP=0
HUB_WEBHOOK_ALLOW_PRIVATE=0
HUB_WEBHOOK_TIMEOUT=5
```

- به‌صورت پیش‌فرض فقط HTTPS پذیرفته می‌شود.
- آدرس‌های loopback/private/link-local و hostnameهایی مثل `localhost` یا `*.internal` رد می‌شوند. هنگام تحویل، DNS دوباره بررسی می‌شود تا SSRF/DNS rebinding محدود شود.
- redirect دنبال نمی‌شود.
- URL، secret و متن پیام در لاگ‌های Hub ثبت نمی‌شوند.
- `webhook_secret` فقط یک‌بار در پاسخ ثبت/تعویض برگردانده می‌شود و در GET عمومی نمایش داده نمی‌شود.
- برای توسعه محلی می‌توان `HUB_WEBHOOK_ALLOW_HTTP=1` و در صورت نیاز `HUB_WEBHOOK_ALLOW_PRIVATE=1` را موقتاً فعال کرد.

### ۶.۶. محدودیت‌ها

- اگر اپراتور آنلاین باشد، پیام به webhook نمی‌رود؛ بنابراین Planet تاریخچهٔ کامل پیام‌های آنلاین را ندارد، مگر UI اپراتور آن‌ها را ثبت کند. اگر Planet باید system of record کامل باشد، webhook باید برای همهٔ پیام‌های ورودی صدا زده شود.
- بین بررسی اتاق و emit ممکن است اپراتور قطع شود؛ این fallback best-effort است.
- برای چت داخلی، webhook پیام مشتری را ذخیره می‌کند، ولی تحویل پاسخ اپراتور به مشتریِ قطع‌شده هنوز حل نشده است.
- Bale حدود ۲۴ ساعت/۲٬۰۰۰ آپدیت نگه می‌دارد؛ برای قطعی طولانی‌تر Planet باید HA باشد یا یک صف محلی در `bale_service` اضافه شود.
- Hub هیچ retry queue داخلی ندارد؛ retry در Bale یا کلاینت است.

<a id="status-codes"></a>
## ۷. کدهای وضعیت HTTP

نمونه پاسخ خطای اعتبارسنجی:

```json
{
  "status": false,
  "message": "Invalid project token."
}
```

| کد | معنی |
| --- | --- |
| 200 | ویرایش، حذف، تعویض توکن یا دریافت کلید موفق |
| 201 | عضویت یا ثبت منبع موفق |
| 400 | JSON نامعتبر در یکی از APIهای نوشتن |
| 401 | توکن پروژه وجود ندارد یا نامعتبر است |
| 403 | شناسه پروژه با توکن مطابقت ندارد |
| 404 | منبع یا پروژه متعلق به توکن یافت نشد |
| 409 | منبع تکراری یا تعارض ثبت‌نام |
| 422 | فیلد یا داده نامعتبر؛ یا اعتبارسنجی منبع ناموفق |
| 429 | عبور از حد مجاز درخواست |
| 500 | خطای داخلی پیش‌بینی‌نشده (پاسخ JSON) |
| 501 | عملیات واتساپ پیاده‌سازی نشده |
| 502 | API پلتفرم در دسترس نیست یا پاسخ نامعتبر داد |
| 503 | بررسی سلامت دیتابیس ناموفق بود، یا Hub پیام Bale را نپذیرفت و سرویس بله باید دوباره تلاش کند |

خطاهای `/api/` و استثناهای کنترل‌شده به‌صورت JSON برگردانده می‌شوند؛ مسیرهای ناموجود زیر `/api/` نیز پاسخ JSON با کد 404 دارند. مسیرهای خارج از `/api/` ممکن است پاسخ پیش‌فرض aiohttp داشته باشند.

نرخ‌محدودی HTTP برای هر IP اعمال می‌شود: عضویت ۵ درخواست در دقیقه، تعویض توکن پروژه و ثبت/حذف webhook پروژه ۱۰ درخواست در دقیقه و سایر APIهای نیازمند احراز هویت ۳۰ درخواست در دقیقه. پاسخ 429 هدر `Retry-After` دارد.

<a id="scope"></a>
## ۸. محدوده نسخه فعلی

- سه جدول users، projects و sources؛ `webhook_secret` و `last_update_id` برای بله در همان جدول sources و webhook پروژه (`webhook_url`، `webhook_secret`، `webhook_updated_at`) در جدول projects.
- عضویت اولیه، ویرایش کاربر و پروژه، ثبت/ویرایش توکن/حذف منبع، تعویض توکن پروژه، ثبت/حذف webhook پروژه و دریافت کلید امضای کلاینت.
- اتصال اپراتور با توکن پروژه؛ اتصال کلاینت با هویت امضاشده `robot_id + user_id`؛ مسیریابی پیام هفت‌فیلدی به پروژه و گفتگوی مربوطه.
- اتصال bridge سرویس‌به‌سرویس بله با `HUB_BALE_SERVICE_TOKEN`؛ فقط یک bridge فعال برای هر provider.
- سرویس مستقل بله: خواندن منابع، ثبت/حذف webhook، دریافت آپدیت، dedup با `update_id`، ارسال پیام و فایل به بله و صف ترتیبی هر ربات.
- نرخ‌محدودی در حافظه برای مسیرهای حساس و محدودیت اندازه/نرخ پیام.
- بدون Redis، صف پایدار، ذخیره تاریخچه پیام، اپراتور یا کلاینت در دیتابیس.
- اگر هیچ اپراتوری در اتاق پروژه آنلاین نباشد، پیام ورودی با webhook امضاشده به Planet فرستاده می‌شود؛ Hub خودش پیام یا صف نگه نمی‌دارد. بدون webhook یا بدون پاسخ 2xx، پیام ACK موفق نمی‌گیرد.
- بله فقط چت خصوصی را پشتیبانی می‌کند؛ گروه/کانال، delivery ACK نهایی و ذخیره فایل در S3 هنوز پیاده‌سازی نشده‌اند.
- ثبت منبع برای بله باعث تنظیم خودکار webhook توسط سرویس بله می‌شود. سایر پیام‌رسان‌ها هنوز آداپتور و ارسال پیام فعال ندارند.
- توقف/راه‌اندازی مجدد Hub همه اتاق‌ها و اتصال‌های Socket.IO را از بین می‌برد؛ اجرای چندپروسه‌ای پشتیبانی نمی‌شود. سرویس بله نیز باید تک‌نمونه اجرا شود.
- ورود کاربر با ایمیل/رمز، بازیابی رمز، جلوگیری از عضویت تکراری، ساخت پروژه دوم و خواندن فهرست/جزئیات پروژه‌ها هنوز پیاده‌سازی نشده‌اند. رمز عبور برای ورود آینده ذخیره می‌شود.
- توکن منابع پیام‌رسان در دیتابیس به‌صورت متنی ذخیره می‌شود؛ امنیت دیتابیس و بکاپ‌ها بر عهده نصب‌کننده است.
- صفحه‌های HTML دمو در این نسخه وجود ندارند؛ کلاینت‌ها باید قرارداد REST/Socket.IO این مستند را پیاده‌سازی کنند.
- تست‌های واحد برای اعتبارسنجی، امضای کلاینت، محدودیت پیام، نرخ‌محدودی، bridge بله، webhook سرویس بله و webhook آفلاین پروژه در `tests/` قرار دارند. تست integration با MariaDB و بله واقعی در مخزن نیست.

این مستند مربوط به پیاده‌سازی کنونی مخزن است، نه تأیید تست عملیاتی یا استقرار روی سرور.
