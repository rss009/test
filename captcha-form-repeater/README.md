# Captcha Form Repeater

A Chrome (Manifest V3) extension that repeats a captcha-protected ASP.NET WebForms POST.
Each iteration:

1. Clears the browser's cookies for the target host, so the next request gets a new `ASP.NET_SessionId`.
2. GETs the form page and reads `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION` and every other hidden input.
3. GETs the captcha image (taken from the page's `<img>`, or the URL you set) on that same session.
4. Waits for you to read the captcha and type it.
5. POSTs `multipart/form-data` with the fresh hidden fields, your extra fields and `txtCaptcha` (or your field name).

The captcha is never solved automatically. You read it and type it, so the tool only tests the
server-side behaviour (session binding, captcha reuse, state handling).

## Install

1. Open `chrome://extensions`, enable **Developer mode**.
2. Click **Load unpacked** and select this `captcha-form-repeater` folder.
3. Click the extension's toolbar icon. A runner tab opens.

## Use

- **Form page URL**: the page that issues the session and hidden fields, e.g. `https://examplelab.com/Form.aspx`.
- **Captcha URL**: leave blank to use the captcha `<img>` found on the page (recommended, since the
  token in the URL can change per page load).
- **Captcha field name**: `txtCaptcha` by default.
- **Extra form fields**: one `name=value` per line, e.g. the submit button `btnSubmit=Submit`.
- **Success marker**: optional text that appears only on success; the log shows `FOUND` or `missing`.
- **Repeat count** and **delay** control how many iterations run.

Press **Start**, read the captcha shown, type it, press Enter or **Submit**. Each result is logged with
the HTTP status, final URL, page `<title>`, response size and the captcha you typed.

## Notes and limits

- The tool clears **all** cookies for the target host in your browser, so it also logs you out of that site.
  Use a separate browser profile for testing.
- `host_permissions` is `<all_urls>`. Narrow it in `manifest.json` to your own domains if you prefer.
- Browsers hide `Set-Cookie` from page scripts, so the session is managed through the browser cookie jar.
  If the site sets its cookies with `SameSite` or `Secure` attributes that block extension requests,
  the session may not persist; check the log for the captcha load failing.
- Test only systems you own or have written authorization to test.
