# Render Free Plan Deployment

This project can run on Render as a Python Web Service.

## Web Service Settings

Use these values when creating the service:

```text
Runtime/Language: Python 3
Build Command: pip install -r requirements.txt
Start Command: gunicorn -c gunicorn.conf.py main:app
Instance Type: Free
```

The Gunicorn config binds to Render's `PORT` environment variable automatically.

## Required Environment Variables

Set these in Render Dashboard > your service > Environment:

```text
FLASK_ENV=production
SECRET_KEY=<strong random secret>
DATABASE_URL=<Render Postgres external or internal database URL>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong admin password>
ADMIN_EMAIL=admin@smarkafrica.com
MAIL_DEFAULT_SENDER=noreply@smark-africa.com
```

Recommended:

```text
REDIS_URL=memory://
WEB_CONCURRENCY=1
LOG_LEVEL=INFO
```

`WEB_CONCURRENCY=1` is friendlier to Render's free instance memory limit.

## Important Free Plan Notes

Render free web services spin down after inactivity and use an ephemeral local
filesystem. Do not use local SQLite for deployed data. Use Render Postgres for
`DATABASE_URL`.

Render free web services do not support dashboard shell or SSH. If you need to
reset the admin password, use the one-shot environment variable method below.

## Recover Admin Access

If login says the default credentials are invalid:

1. Open the service in Render Dashboard.
2. Go to Environment.
3. Set `ADMIN_PASSWORD` to the new password you want.
4. Add `ADMIN_RESET_PASSWORD=1`.
5. Save and deploy.
6. After the deploy finishes, log in with `ADMIN_USERNAME` and the new password.
7. Remove `ADMIN_RESET_PASSWORD`.
8. Save and deploy again.

Leaving `ADMIN_RESET_PASSWORD=1` enabled will reset the admin password on every
app start, so remove it after recovery.

## Why Defaults Fail After Deployment

The app creates the admin user on first startup. After that, changing
`ADMIN_PASSWORD` does not update the existing database password hash unless
`ADMIN_RESET_PASSWORD=1` is set for one deploy.
