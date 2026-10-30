# Connect Gmail: Google Cloud OAuth client

JobPilot reads your inbox and adds `JobPilot/*` labels with the Gmail API. Google requires an OAuth
client that *you* own. It stays in "Testing" mode, so no Google review is needed — only the
addresses you list as test users can sign in.

Time: about 10 minutes. Everything happens at <https://console.cloud.google.com>, signed in with the
Google account whose Gmail you want to triage.

## 1. Create a project

1. Top bar → project picker → **New project**.
2. Name: `JobPilot` · Location: *No organization* → **Create**, then select it in the picker.

## 2. Enable the Gmail API

1. Menu → **APIs & Services → Library**.
2. Search **Gmail API** → open it → **Enable**.

## 3. Configure the consent screen (Google Auth Platform)

1. Menu → **APIs & Services → OAuth consent screen** (labelled *Google Auth Platform* in recent
   consoles) → **Get started**.
2. **App information**: App name `JobPilot`, User support email = your address → Next.
3. **Audience**: **External** → Next.
4. **Contact information**: your address → Next → accept the policy → **Create**.
5. **Audience** tab → **Test users** → **Add users** → your Gmail address → Save.
   Leave the publishing status on **Testing**.
6. **Data access** tab → **Add or remove scopes** → add these two, then **Update** and **Save**:
   - `https://www.googleapis.com/auth/gmail.modify` — read mail and add labels (triage)
   - `https://www.googleapis.com/auth/gmail.send` — only used from Phase 7, when you enable sending

   JobPilot asks for them **separately**: triage never holds the send permission (ADR-0002/0003).

## 4. Create the Desktop OAuth client

1. **Clients** tab → **Create client**.
2. Application type: **Desktop app** · Name: `JobPilot local` → **Create**.
3. **Download JSON** on the confirmation dialog (or later from the client's page).
4. Save the file as:

   ```
   JobPilot/secrets/google_credentials.json
   ```

   `secrets/` is gitignored. Never commit or share this file.

## 5. Connect

```bash
make gmail-auth
```

A browser tab opens on Google's consent page. Because the app is in Testing mode you will see
*"Google hasn't verified this app"* → **Continue** (it is your own app). Approve the Gmail scope.
The token is encrypted with your `FERNET_KEY` and stored in the database; the terminal prints the
connected address.

## Good to know

- **Tokens of apps in Testing mode expire after 7 days.** When triage reports
  `gmail auth expired`, run `make gmail-auth` again. (Publishing the app to "In production" removes
  this limit but `gmail.modify` then needs Google's security review — not worth it for personal use.)
- Revoke access any time at <https://myaccount.google.com/permissions>.
- JobPilot never deletes, trashes, forwards or archives mail; it only adds labels under `JobPilot/`.
