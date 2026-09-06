# Finance Hub

> **Public repository note:** This is a sanitized public copy of a private
> personal-finance project I developed over several months through more than
> one hundred commits. The original repository and its full history remain
> private. This public repository starts with a fresh Git history and contains
> only reviewed, generalized source code and synthetic example data.

Finance Hub is a local, single-user personal finance tracker for NZD and USD
accounts. It runs on Windows at `http://127.0.0.1:8000` and keeps private
finance data outside the Git repository in `%LOCALAPPDATA%\FinanceHub`.

The app includes:

- A dashboard with net worth, monthly income and spending, budget progress,
  recurring items, recent transactions, and bank refresh status.
- Manual, CSV-imported, Akahu, and Plaid accounts.
- Transaction review, category assignment, split transactions, and reusable
  category rules.
- Monthly budgets with planned dollars, planned percentages,
  rollover controls, group totals, and smooth in-place saving.
- Cash-flow and report views with donut, bar, combination, and net-worth
  charts.
- Timeline choices for this month, 3 months, 6 months, 1 year, 3 years,
  5 years, and all available history.
- Recurring forecasts, savings goals, investment holdings, sync diagnostics,
  backups, and optional daily email alerts.
- Responsive desktop, tablet, and phone layouts, including mobile navigation
  and scrollable data views where a table or chart needs more width.

## Privacy and safety model

All committed seed data and test fixtures in this public edition are generalized
or synthetic. Never commit real bank data, credentials, raw exports, account
identifiers, email addresses, API tokens, or generated local data.

By default, private files are stored in:

```text
%LOCALAPPDATA%\FinanceHub
```

That folder holds the SQLite database and, when created, backups, exports,
scheduled-task helpers, optional Tailscale access settings, and encrypted local
secrets. Bank tokens and email passwords use Windows user-profile encryption.
The web server always binds only to `127.0.0.1`, so it is not exposed to the
local network. Optional phone access uses a private Tailscale Serve proxy and
an exact Tailscale user allowlist; it never changes the app to a public server.

The source repository may be inside OneDrive. Private data should stay outside
the repository and outside OneDrive. Advanced users can set
`FINANCE_HUB_DATA_DIR` before running the app to choose another private folder,
but the folder must not be inside this project or a OneDrive path.

## 1. Install the required programs

Finance Hub is built for Windows 10 or 11. Use a current browser such as
Microsoft Edge, Chrome, or Firefox.

Recommended versions for a new installation:

- Git for Windows: current maintained release.
- Python 3.14. The code supports Python 3.11 through 3.14.
- Node.js 24 LTS or another supported LTS release at Node 22.12 or newer.

The locked Vite toolchain declares Node `^20.19.0 || >=22.12.0`. Node 20 now
being end-of-life, new installations should use a current Node LTS release,
not Node 20.

### Install with winget

Open **Command Prompt** and run:

```cmd
winget --version
winget install --id Git.Git -e --source winget
winget install --id Python.Python.3.14 -e --source winget
winget install --id OpenJS.NodeJS.LTS -e --source winget
```

If `winget` is unavailable, use the official download pages:

- [Git for Windows](https://git-scm.com/install/windows)
- [Python downloads for Windows](https://www.python.org/downloads/windows/)
- [Node.js downloads](https://nodejs.org/en/download)

When installing Python manually, make sure the Python launcher is available.
Node.js includes `npm`.

Close and reopen Command Prompt after installing, then verify the tools:

```cmd
git --version
py -3 --version
node --version
npm --version
```

If `py -3 --version` fails but `python --version` works, replace `py -3` with
`python` when creating the virtual environment below.

## 2. Clone the repository

In Command Prompt, move to the folder where the source code should live and
run:

```cmd
git clone https://github.com/ColyanM/personal-finance-tracker-public.git
cd personal-finance-tracker-public
```

All remaining commands assume the current directory is the repository root.

## 3. Create the Python environment

Create and activate a repository-local virtual environment:

```cmd
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The requirements file installs `tzdata`. It is required on Windows because the
daily automation uses the `Pacific/Auckland` IANA timezone and Windows does not
provide that database to Python's `zoneinfo` module. Installing it also allows
the full test suite to be collected.

Keep this Command Prompt open. In each new Command Prompt, return to the repo
and reactivate the environment before running Python commands:

```cmd
cd personal-finance-tracker-public
.venv\Scripts\activate.bat
```

PowerShell users can activate the same environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

## 4. Review or customize the public defaults

The public edition uses neutral interface branding, generalized starter
categories and rules, and synthetic test fixtures. Review these files before
creating the database if different defaults are wanted:

```text
frontend\src\main.jsx
data\categories_seed.json
scripts\category_rules.py
scripts\bnz_csv_import.py
scripts\monarch_import.py
```

The first file contains visible interface labels. The remaining files contain
generalized starter categories, merchant rules, and import mappings. Details
and limitations are in [Customize branding](#customize-branding) and
[Customize seed data](#customize-seed-data).

## 5. Create local app data

Create or migrate the private SQLite database, install the offline FX fallback,
and seed categories and category rules:

```cmd
python scripts\db_init.py
python scripts\fx_seed.py seed
python scripts\categories.py seed
python scripts\category_rules.py seed
python scripts\fx_refresh.py latest
```

The first four commands work without bank credentials. The last command uses
the public Frankfurter FX service. If the internet is unavailable, the app can
continue with the saved fallback rate and refresh it later.

These commands create data under `%LOCALAPPDATA%\FinanceHub`, not in the Git
repository.

## 6. Install and build the frontend

The repository includes `frontend\package-lock.json`, so use `npm ci` for a
repeatable install:

```cmd
cd frontend
npm ci
npm run build
cd ..
```

The Python server serves the built files from `frontend\dist`. Running only
the React source without building it will not update the normal app at port
8000.

## 7. Verify the installation

Run the local safety checks, final readiness check, timezone check, and tests:

```cmd
python scripts\security_check.py
python scripts\final_check.py
python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Pacific/Auckland'))"
python -m unittest discover -s tests
```

The timezone command should print `Pacific/Auckland`. Fix any failed security
or final checks before connecting bank providers.

## 8. Start Finance Hub

From the repository root, with the virtual environment activated, run:

```cmd
python app.py
```

Then open:

```text
http://127.0.0.1:8000
```

The app refreshes the public FX rate in the background when it starts and falls
back to the last saved rate if the refresh fails. Stop the server with `Ctrl+C`.

## Private phone access with Tailscale

[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) can make
the loopback app available over private tailnet HTTPS without opening a router
port or binding Finance Hub to the LAN. This is the supported remote-access
model. Do not use Tailscale Funnel, port forwarding, or `HOST = "0.0.0.0"`.

Remote access uses two independent checks:

1. Tailscale admits a device to the private tailnet and adds its authenticated
   `Tailscale-User-Login` header.
2. Finance Hub permits only the exact configured `*.ts.net` hostname and login.
   Remote writes must also have the exact HTTPS `Origin` or `Referer`.

Tailscale Serve strips caller-supplied identity headers before adding its own.
Funnel does not add those identity headers, so Finance Hub rejects accidental
public requests. The backend must remain on `127.0.0.1` for that trust boundary
to remain valid.

### Install and enable private access

1. [Install Tailscale on Windows](https://tailscale.com/docs/install/windows),
   sign in, and install Tailscale on the phone using the same identity.
2. Stop a manually running `python app.py` process before creating the startup
   task below.
3. Open **Terminal (Administrator)** in the repository. Tailscale requires an
   elevated Windows terminal to configure Serve.
4. Preview the exact URL, user, local target, and command:

```cmd
python scripts\tailscale_access.py plan
```

5. Enable persistent, private HTTPS Serve access:

```cmd
python scripts\tailscale_access.py enable --confirm ENABLE_PRIVATE_ACCESS
```

The first Serve setup may open a Tailscale consent page to enable tailnet HTTPS.
The helper refuses to continue if it finds an active public Funnel listener. It
saves only the non-secret URL and login under
`%LOCALAPPDATA%\FinanceHub\tailscale_access.json`.

6. Preview and install the current-user Windows logon task that starts Finance
   Hub. It must run as this user, not `SYSTEM`, because bank and email secrets
   are encrypted for the current Windows profile.

```cmd
python scripts\app_scheduler.py preview
python scripts\app_scheduler.py install --confirm CREATE --start-now
```

7. Print the private URL and verify that Funnel is off:

```cmd
python scripts\tailscale_access.py status
python scripts\app_scheduler.py status
tailscale funnel status
```

Open the printed `https://<device>.<tailnet>.ts.net` address from the phone. The
phone menu and narrow-screen layouts use the same relative `/api` routes, so no
separate mobile build is needed.

### Keep the laptop available with its lid closed

Tailscale cannot wake a sleeping laptop. In Windows power settings, change only
the **Plugged in** action for closing the lid to **Do nothing**. Leave the
battery action as **Sleep**, keep the laptop plugged in, and place it somewhere
ventilated rather than inside a bag. Windows can be locked; do not sign out.

The app task starts at user logon and continues while that Windows session is
locked. After a reboot, sign in once before expecting phone access. Background
Tailscale Serve itself persists across reboots and Tailscale restarts because
the helper uses `--bg`.

### Check, stop, or remove phone access

```cmd
python scripts\tailscale_access.py status
python scripts\app_scheduler.py status
python scripts\app_scheduler.py stop
python scripts\tailscale_access.py disable --confirm DISABLE_PRIVATE_ACCESS
python scripts\app_scheduler.py remove --confirm REMOVE
```

The app log is retained outside Git at:

```text
%LOCALAPPDATA%\FinanceHub\finance_hub_web_app.log
```

If `tailscale funnel status` reports a public mapping, review whether it belongs
to another service before using `tailscale funnel reset`; that reset affects all
Funnel mappings on the laptop. Finance Hub's helper never enables Funnel and
never resets unrelated Funnel configuration automatically.

## Full first-time command list

This is the complete command sequence when the default seed data is wanted:

```cmd
git clone https://github.com/ColyanM/personal-finance-tracker-public.git
cd personal-finance-tracker-public
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python scripts\db_init.py
python scripts\fx_seed.py seed
python scripts\categories.py seed
python scripts\category_rules.py seed
python scripts\fx_refresh.py latest
cd frontend
npm ci
npm run build
cd ..
python scripts\security_check.py
python scripts\final_check.py
python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Pacific/Auckland'))"
python -m unittest discover -s tests
python app.py
```

## First-use workflow

Bank connections are optional. A new user can begin with a manual account:

```cmd
python scripts\accounts.py add --name "Everyday" --institution "Example Bank" --account-type checking --balance-type asset --currency NZD --balance 1000.00
python scripts\accounts.py list
```

Use `--balance-type liability` for a credit card or loan balance. Amounts are
entered in major units, so `1000.00` means one thousand dollars.

A normal app session is:

1. Activate `.venv` and start the app with `python app.py`.
2. Open `http://127.0.0.1:8000`.
3. Use **Refresh all** to refresh configured providers. Providers that are not
   fully configured are reported as skipped, so the button is also safe before
   any bank is connected.
4. Review new transactions and correct categories.
5. Create or adjust rules for repeated merchants.
6. Check Budget, Cash Flow, Reports, Recurring, Investments, and Goals.

The desktop sidebar can be pinned. On a phone or narrow window, use the menu
button beside the page title. Tables and charts retain aligned labels and
values; wide data views may scroll horizontally on small screens.

## Budget behavior

The Budget page is monthly and defaults to the current month. Planned income
should be entered first because expense percentages are based on planned income
for the selected month.

For each budget category:

- **Planned $** saves a dollar amount.
- **Planned %** saves a percentage of planned income.
- Saving either field recalculates the other from planned income.
- The note below the category shows its percentage of planned income.
- Rollover can carry eligible unused budget into the next period.

Each group header, including groups such as Variable Expenses, shows:

- Total planned dollars.
- The group's total planned percentage of planned income.
- Actual spending or income.
- Remaining budget.

The group percentage is calculated from the unrounded group total rather than
adding rounded row percentages. When no planned income exists, expense
percentage entry is disabled and percentage totals display as unavailable.

Budget edits save in place. Existing rows remain visible while the server
refreshes, queued edits are applied in order, and a small update status replaces
the previous full-page flash.

## Charts, timelines, and responsive views

Net Worth, Cash Flow, and Reports expose a timeline selector with:

- This month
- 3 months
- 6 months
- 1 year
- 3 years
- 5 years
- All available history

Depending on the page, category data can be shown as donut or bar charts and
cash flow as a combination chart. Chart points, bars, segments, and report rows
can link to the matching Transactions date range or category. Long timelines
reduce label density and scale bar widths so points remain readable.

Layouts adapt for desktop, tablet, and phone widths. Navigation becomes a
mobile menu, summary cards stack, transaction fields receive mobile labels, and
wide charts or structured tables use controlled horizontal scrolling rather
than clipping values. The app also honors the browser's reduced-motion setting.

## Customize branding

The public edition uses neutral Finance Hub labels. To change the visible
branding, edit the labels in:

```text
frontend\src\main.jsx
```

Look for the `Your financial overview` heading, the sidebar footer label, and
the `F` profile mark.
Rebuild the frontend after editing:

```cmd
cd frontend
npm run build
cd ..
```

To change the starter financial defaults, review:

- `data\categories_seed.json` for generalized category names.
- `scripts\category_rules.py` for generalized merchant-to-category rules.
- `scripts\bnz_csv_import.py` for generic BNZ import mappings.
- `scripts\monarch_import.py` for generalized Monarch category mappings.

Changing a category name in only one file can cause a seed or import mapping to
fall back to `Uncategorized`. Keep names consistent across the files used by
the intended import path. Keep personal names, employer details, account labels,
and real merchant history in local application data rather than committed source
files.

## Customize seed data

### Categories

Starter categories are in:

```text
data\categories_seed.json
```

Each entry has this form:

```json
{"name": "Groceries", "group_name": "Variable Expenses", "type": "expense"}
```

Allowed types are `income`, `expense`, and `transfer`.

Important limitations:

- Category names must be unique.
- Keep `Uncategorized` unless the fallback behavior is also changed in code.
- Budget categories created in the Budget page can be income or expense, not
  transfer categories.
- `python scripts\categories.py seed` only adds missing names. It does not
  rename or update an existing category with the same name.
- Rename or deactivate categories instead of deleting database rows after
  transactions, budgets, splits, or rules refer to them.

Useful commands:

```cmd
python scripts\categories.py list
python scripts\categories.py add --name "New category" --group "Variable Expenses" --type expense
python scripts\categories.py update --name "Old name" --new-name "New name"
python scripts\categories.py update --name "Category" --group "Fixed Expenses"
python scripts\categories.py update --name "Category" --type transfer
python scripts\categories.py deactivate --name "Category"
```

### Category rules

Starter rules are the `STARTING_RULES` list in:

```text
scripts\category_rules.py
```

Each rule is a `(text, category, priority)` tuple:

```python
("Text to match", "Category name", 100)
```

The category must already exist. Lower priority numbers run first, so specific
payment and transfer rules should have lower numbers than broad merchant rules.

```cmd
python scripts\category_rules.py list
python scripts\category_rules.py add --text "Merchant text" --category "Groceries" --priority 100
python scripts\category_rules.py apply
python scripts\category_rules.py summary
python scripts\category_rules.py deactivate --id RULE_ID
```

Running `python scripts\category_rules.py seed` again only adds missing rule
text. It does not update or reactivate an existing rule.

### Exchange-rate fallback

The offline starter rates are in:

```text
data\fx_rates_seed.json
```

Most users should keep them as an emergency fallback and refresh the public
rate:

```cmd
python scripts\fx_refresh.py latest
```

Historical rates can be backfilled for older imported data:

```cmd
python scripts\fx_refresh.py historical --start 2024-01-01 --end 2024-12-31
```

### Reset seed data during an empty setup

Only reset the database before real data matters. Stop the app and create a
backup first if anything might be needed:

```cmd
python scripts\database_backup.py backup
```

Then remove the private database and initialize it again:

```cmd
del "%LOCALAPPDATA%\FinanceHub\finance_hub.sqlite"
python scripts\db_init.py
python scripts\fx_seed.py seed
python scripts\categories.py seed
python scripts\category_rules.py seed
python scripts\fx_refresh.py latest
```

Deleting the database removes accounts, transactions, budgets, goals, balance
history, settings, and other local app data.

## Import data without linking a bank

Review the supported CSV formats and required confirmation flags before an
import:

```cmd
python scripts\import_csv_transactions.py --help
python scripts\bnz_csv_import.py --help
python scripts\monarch_import.py --help
```

Use preview modes where offered. Create a backup before importing and be
careful when an import overlaps a live-provider date range, because the two
sources can create duplicate-looking history.

After an import, inspect:

```cmd
python scripts\sync_status.py
python scripts\category_rules.py summary
```

## Optional bank connections

Manual accounts and CSV imports work without bank access. Bank connections are
read only; Finance Hub does not store online-banking passwords and does not
request payment or transfer permission.

Use this order for every provider:

1. Finish local setup, build, and verification.
2. Save encrypted credentials with that provider's setup script.
3. Create a database backup.
4. Run the pre-bank review for that provider.
5. Perform the provider's remote credential check or link flow.
6. Preview available accounts and save only the intended selection.
7. Preview transactions where supported, then perform a short refresh.
8. Review Sync and Transactions before increasing the refresh range.

The provider-specific review includes the security baseline and refuses to pass
without a valid recent backup and that provider's encrypted credentials.

### BNZ and other New Zealand accounts through Akahu

Akahu requires an App ID Token and User Access Token. Create a Personal App in
the [Akahu developer dashboard](https://my.akahu.nz/developers) and consult the
[official Akahu authentication guide](https://developers.akahu.nz/me/reference/api-akahu-io-authentication)
if those values are unfamiliar. The conservative setup order is:

```cmd
python scripts\akahu_setup.py setup
python scripts\database_backup.py backup
python scripts\pre_bank_review.py --provider bnz
python scripts\akahu_setup.py status
python scripts\akahu_accounts.py preview
python scripts\akahu_accounts.py save
python scripts\bnz_refresh.py --days 30 --confirm REFRESH
```

The save command asks which accounts to store and requires an explicit `SAVE`
confirmation before writing rows.

### US accounts through Plaid Production

Finance Hub uses Plaid Production as its only Plaid runtime. Create a Plaid
account in the [Plaid Dashboard](https://dashboard.plaid.com/signup) and continue
only after Plaid approves access to the real institutions you intend to connect.
Copy the Production Client ID and Production secret from the API Keys page, then
use this conservative setup order:

```cmd
python scripts\plaid_setup.py setup
python scripts\database_backup.py backup
python scripts\pre_bank_review.py --provider plaid
python scripts\plaid_setup.py review
python scripts\plaid_setup.py check
python scripts\plaid_link.py connect
python scripts\plaid_accounts.py preview
python scripts\plaid_accounts.py save --accounts "1,2" --confirm SAVE
python scripts\plaid_transactions.py preview --days 30
python scripts\plaid_transactions.py refresh --days 30 --confirm REFRESH
```

Replace `1,2` with the Production account selections shown by the preview.
Review the institution and every selected account before confirming `SAVE`.
Each refresh repeats the provider-specific safety review and creates another
backup before saving remote data. Plaid access remains read only and does not
request payment or transfer permission.

No Plaid environment variables are required. Plain `python app.py`, the
**Refresh all** button, direct Plaid commands, and scheduled refreshes all use
Production automatically.

If a refresh reports `ITEM_LOGIN_REQUIRED`, repair the existing connection
through Plaid Link update mode. Do not use `connect` again and do not disconnect
the item. First use the connection number named in the refresh error, or list
the saved connection labels locally:

```cmd
python scripts\plaid_items.py list
```

Then start a one-time repair session for that connection number:

```cmd
python scripts\plaid_link.py update --connection N
```

The command opens `http://127.0.0.1:8765` on the laptop. Select **Reconnect**,
complete the bank sign-in, MFA, or consent prompt, and wait for the repaired
message. Update mode keeps the existing Plaid Item and encrypted access token;
it does not create accounts or write finance records. Close the temporary page,
return to Finance Hub at `http://127.0.0.1:8000`, and run **Refresh all** again.

One unavailable Plaid connection does not block the other US institutions.
Finance Hub saves data only from Items that completed every account and
transaction request, leaves the failed Item's balances, pending transactions,
and investments unchanged, and names each skipped institution in the refresh
message. If every saved Item fails, the US refresh fails without saving a
partial result. Scheduled automation treats a partial US refresh as needing
attention so the institution name and local repair command are included in the
failure notice.

## Daily transaction alerts

Daily email is optional. Current email delivery uses Yahoo SMTP. Create a
[Yahoo app password](https://help.yahoo.com/kb/mail/generate-password-sln15241.html)
and use it instead of the normal Yahoo account password.

The scheduled daily status can include refresh results, new posted
transactions, month-to-date budget status, current net worth, and the change
from the prior day.

### Configure and enable email

First save and test the encrypted email credentials:

```cmd
python scripts\send_daily_transaction_alert.py setup-email
python scripts\send_daily_transaction_alert.py email-status
python scripts\send_daily_transaction_alert.py preview --hours 24
python scripts\send_daily_transaction_alert.py send-email --hours 24 --send-if-empty --confirm SEND
```

Then start the app, open **Settings**, set **Daily transaction alerts** to
**On**, choose the alert time, currency, lookback, row count, and whether to
send when there are no new transactions, and click **Save settings**. The
Settings page can also send a test email and shows the current alert preview.

Confirm the automation plan without contacting a bank or sending mail:

```cmd
python scripts\daily_automation.py plan
```

If email remains Off in Settings, the daily automation will skip it even when
the scheduler command does not include `--skip-email`.

### Install a Windows scheduled task

Preview before creating a task:

```cmd
python scripts\task_scheduler.py preview --time 20:00 --send-if-empty
```

Examples below use 8 PM local computer time. Windows Task Scheduler uses the
computer's timezone, so confirm Windows is set to Auckland/Wellington if 8 PM
New Zealand time is intended.

Scheduled Plaid refreshes use the same Production-only configuration as the web
app. No Plaid environment selection or session setup is required.

All configured providers plus email:

```cmd
python scripts\task_scheduler.py install --confirm CREATE --time 20:00 --send-if-empty
```

Email only, with no bank refresh:

```cmd
python scripts\task_scheduler.py install --confirm CREATE --time 20:00 --skip-bnz --skip-plaid --send-if-empty
```

BNZ/Akahu plus email, with Plaid skipped:

```cmd
python scripts\task_scheduler.py install --confirm CREATE --time 20:00 --skip-plaid --send-if-empty
```

Plaid plus email, with BNZ/Akahu skipped:

```cmd
python scripts\task_scheduler.py install --confirm CREATE --time 20:00 --skip-bnz --send-if-empty
```

BNZ/Akahu refresh only:

```cmd
python scripts\task_scheduler.py install --confirm CREATE --time 20:00 --skip-plaid --skip-email
```

Plaid refresh only:

```cmd
python scripts\task_scheduler.py install --confirm CREATE --time 20:00 --skip-bnz --skip-email
```

Changing the alert time in Settings does not rewrite an existing Windows task.
Run the install command again to update it.

Check or remove the task and inspect automation history:

```cmd
python scripts\task_scheduler.py status
python scripts\automation.py history
python scripts\task_scheduler.py remove --confirm REMOVE
```

## Backups

Create a backup before imports, provider changes, migrations, repairs, or
restores:

```cmd
python scripts\database_backup.py backup
python scripts\database_backup.py list
```

Stop the web app before restoring:

```cmd
python scripts\database_backup.py restore BACKUP_FILE_NAME --confirm RESTORE
```

Old backups can be removed only with the explicit confirmation phrase:

```cmd
python scripts\database_backup.py cleanup --keep 10 --confirm DELETE_OLD_BACKUPS
```

## Updating or developing the app

Before pulling or changing database code, create a backup. After source updates,
reactivate the environment, install any current dependencies, migrate the
database, and rebuild:

```cmd
.venv\Scripts\activate.bat
python scripts\database_backup.py backup
git pull
python -m pip install -r requirements.txt
python scripts\db_init.py
cd frontend
npm ci
npm run build
cd ..
python scripts\final_check.py
python app.py
```

For frontend development, run the Python API in one Command Prompt:

```cmd
.venv\Scripts\activate.bat
python app.py
```

Open a second Command Prompt in the repository root and run Vite:

```cmd
cd frontend
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8000`. Use the local URL printed by
Vite while developing. Rebuild with `npm run build` before returning to the
normal app URL at port 8000.

## Checks and tests

Run backend checks from the repository root with `.venv` activated:

```cmd
python scripts\security_check.py
python scripts\final_check.py
python -m unittest discover -s tests
```

Build the production frontend:

```cmd
cd frontend
npm run build
cd ..
```

`final_check.py` verifies the security baseline, current database schema,
SQLite integrity, built React assets, settings, private-data location, and
automation helper files. It also verifies that the `Pacific/Auckland` timezone
required by daily automation is available.

## Troubleshooting

### `No module named 'tzdata'` or `ZoneInfoNotFoundError`

Activate the virtual environment and reinstall the repository requirements:

```cmd
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Pacific/Auckland'))"
```

### Node or Vite reports an unsupported engine

Check the version:

```cmd
node --version
```

Install a current Node LTS release. The locked frontend requires Node
`^20.19.0 || >=22.12.0`, and a current 22.x or 24.x LTS release is recommended.
Then reinstall from the lockfile:

```cmd
cd frontend
npm ci
npm run build
cd ..
```

### The app says the frontend has not been built or the browser shows old UI

```cmd
cd frontend
npm ci
npm run build
cd ..
python app.py
```

Reload the browser after the build. Restart `python app.py` as well when Python
backend code changed.

### Port 8000 is already in use

```cmd
netstat -ano | findstr :8000
taskkill /PID PID_FROM_NETSTAT /F
python app.py
```

Only end the process after confirming the PID belongs to the old Finance Hub
server.

### Database missing, schema old, or final check fails

```cmd
python scripts\db_init.py
python scripts\final_check.py
```

`db_init.py` creates a missing database and applies supported schema migrations.
Back up real data before migrating.

### FX refresh fails

The app can use the saved fallback rate. Try again later:

```cmd
python scripts\fx_seed.py seed
python scripts\fx_refresh.py latest
```

### Dashboard, report, or email numbers look wrong

```cmd
python scripts\sync_status.py
python scripts\category_rules.py summary
python scripts\automation.py history
python scripts\database_backup.py backup
```

Inspect the relevant app page before editing or deleting data. Check the
selected currency, timeline, date range, transfer categories, and whether
transactions are pending or posted.

### Daily email does not arrive

```cmd
python scripts\send_daily_transaction_alert.py email-status
python scripts\send_daily_transaction_alert.py preview --hours 24
python scripts\task_scheduler.py status
python scripts\automation.py history
```

Confirm that Daily transaction alerts are On in Settings, the Yahoo app
password is still valid, recipients are correct, Windows uses the intended
timezone, and the installed task time matches Settings. Add `--send-if-empty`
if an email is expected even with no new posted transactions.

### A scheduled run fails because a provider is not configured

Reinstall the task with the unused provider skipped. For example, email only:

```cmd
python scripts\task_scheduler.py install --confirm CREATE --time 20:00 --skip-bnz --skip-plaid --send-if-empty
```

Use the single-provider examples in
[Install a Windows scheduled task](#install-a-windows-scheduled-task) when only
Akahu or Plaid is configured.

## Security notes

- The web app listens only on `127.0.0.1`.
- Optional remote access uses private Tailscale Serve, an exact `*.ts.net`
  origin, and a pinned `Tailscale-User-Login`; all remote GET and POST requests
  are denied without that identity.
- Tailscale Funnel, router port forwarding, and direct LAN binding are not
  supported for this unauthenticated local server.
- Bank integrations request read-only data access.
- Secrets are entered with hidden prompts and encrypted for the current Windows
  user.
- Private databases, backups, exports, raw imports, and tokens must remain out
  of Git.
- Run `security_check.py` during setup and the provider-specific
  `pre_bank_review.py --provider bnz` or `--provider plaid` before remote bank
  access.
- Plaid uses Production only. Keep the provider-specific safety review, recent
  backup, account preview, and explicit `SAVE` or `REFRESH` confirmations in the
  workflow before real bank data is written locally.
- Stop the app before restoring a backup or deleting the database.
