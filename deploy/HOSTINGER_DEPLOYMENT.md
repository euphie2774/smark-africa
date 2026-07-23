# Hostinger Deployment for smark-africa.com

This project is a Flask application. The domain should serve the Flask app as:

- Primary: `https://smark-africa.com`
- Alias: `https://www.smark-africa.com`

## DNS in Hostinger

If the domain was bought at Hostinger and the hosting is also at Hostinger, use Hostinger nameservers and point the domain to the hosting plan from hPanel.

Recommended records:

```text
Type   Name   Value
A      @      <your Hostinger hosting/server IP>
CNAME  www    smark-africa.com
```

If Hostinger gives you nameservers instead of an IP, set those at the domain registrar:

```text
ns1.dns-parking.com
ns2.dns-parking.com
```

DNS propagation can take a few minutes, but allow up to 24 hours.

## Shared Hosting / hPanel Python App

Use this option if your Hostinger plan has "Setup Python App".

1. Upload the project files to the application directory.
2. Create the Python app in hPanel.
3. Set the application root to the uploaded project directory.
4. Set the startup file to `passenger_wsgi.py`.
5. Set the application entry point to `application`.
6. Install dependencies:

```bash
pip install -r requirements.txt
```

7. Add production environment variables in hPanel:

```text
FLASK_ENV=production
SECRET_KEY=<strong random secret>
DATABASE_URL=<production database URL>
ADMIN_USERNAME=<admin username>
ADMIN_PASSWORD=<strong admin password>
ADMIN_EMAIL=admin@smark-africa.com
MAIL_DEFAULT_SENDER=noreply@smark-africa.com
```

8. Restart the Python app from hPanel.
9. Enable SSL for `smark-africa.com` and `www.smark-africa.com`.

### Recover Admin Access

If the deployed login says the default credentials are invalid, reset the stored
admin password on the deployed server. Changing `ADMIN_PASSWORD` in hPanel only
affects new admin creation; it does not automatically change an existing
database user's password hash.

From the Python app terminal, run one of these:

```bash
python reset_admin_password.py --password "NewStrongPassword123!"
```

Or set `ADMIN_PASSWORD` in hPanel, then run:

```bash
python reset_admin_password.py
```

Restart the Python app after the script reports `Password reset complete`, then
log in with the configured `ADMIN_USERNAME` and the new password.

One-shot alternative: set `ADMIN_RESET_PASSWORD=1` in hPanel, restart the app
once, confirm login works, then remove `ADMIN_RESET_PASSWORD` and restart again.

## VPS / Nginx

Use this option if you have a Hostinger VPS.

The included Nginx config expects Gunicorn on port `8000` and serves static files from:

```text
/srv/smarkafrica/static/
```

Start the app with:

```bash
gunicorn -c gunicorn.conf.py main:app
```

Then install `deploy/nginx-smarkafrica.conf` as the Nginx site config and enable SSL for both domains.
