# Registration (independent first step)

Run the existing migration command to create users and projects (0008, 0009).
The registration route does not create legacy clients or channels and does not
change existing WebSocket authentication, routing, Redis or provider behavior.

POST /v1/register on the HTTP service (loopback port 8766).
Content-Type: application/json

```json
{"name":"Owner","phone":"09121234567","email":"owner@example.ir","domain":"example.ir","project_name":"My shop"}
```

Only name and phone are required. Omitted/blank email and domain become NULL.
Project name defaults to the domain when it fits, otherwise the user name.
Domain is a hostname, not a URL, and may be shared by projects.
Phone is normalized to +country-code notation (Iranian local mobile numbers
are normalized to +98). Other numbers should include their country code.
Email and phone are unique; duplicates return 409 and do not create projects.
No password is received, so password is NULL until a future password setup flow.

201 Created:
```json
{"status":true,"api_key":"<64 random hex characters>","message":"Registration successful."}
```

The key is returned once. The projects.apikey field contains its SHA-256 hash.
The user and project are inserted in one database transaction. Invalid fields
return 422; bad JSON 400; conflicts 409; registration rate limit 429.
No login, API-key authentication, ownership verification, domain verification,
OTP, key recovery or authenticated additional project creation is implemented.
The database allows multiple projects per user; registration only creates the
initial project. Do not grant privileges based on the unverified phone/domain.
Keep this route private until the desired registration/abuse policy is confirmed.
No reverse-proxy configuration or production deployment is included.
