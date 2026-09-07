# Finance Hub

> **Public version:** This is the public version of my finance tracker. My full
> work and development history are in a private repo with hundreds of commits.
> This copy starts with a fresh Git history and contains generalized source code
> and synthetic example data.

Finance Hub is a local, single-user personal finance tracker for NZD and USD
accounts. It runs on Windows at `http://127.0.0.1:8000` and keeps private
finance data outside the Git repository in `%LOCALAPPDATA%\FinanceHub`.

The app includes:

- Account balances and net worth.
- Transaction imports, category assignment, splits, and category rules.
- Monthly budgets, cash-flow reports, and spending charts.
- Recurring forecasts, savings goals, and investment holdings.
- Optional bank connections and daily email alerts.

You can start without linking a bank. Setup uses a few commands, and adding
manual accounts, importing files, and making backups still use Command Prompt.
Most day-to-day review happens in the browser. No code changes are needed.

## Contents

- [First-time setup](#first-time-setup)
- [Add an account and import transactions](#add-an-account-and-import-transactions)
- [Using the app](#using-the-app)
- [Starting and stopping the app](#starting-and-stopping-the-app)
- [Backups](#backups)
- [Other import formats](#other-import-formats)
- [Optional bank connections](#optional-bank-connections)
- [Daily transaction alerts](#daily-transaction-alerts)
- [Choose a private data folder](#choose-a-private-data-folder)
- [Customize branding](#customize-branding) and [starter data](#customize-seed-data)
- [Updating or developing the app](#updating-or-developing-the-app)
- [Checks and tests](#checks-and-tests)
- [Troubleshooting](#troubleshooting)
- [Privacy](#privacy) and [security notes](#security-notes)

## First-time setup

Finance Hub is built for Windows 10 or 11. Use a browser such as Edge, Chrome,
or Firefox. You need internet access to install it; saved data and manual
imports can be used offline afterward.

### 1. Install the required programs

Use the official installers:

- [Python for Windows](https://www.python.org/downloads/windows/): Python 3.14.
  This runs the app. Keep the Python launcher/install manager enabled, and
  select **Add python.exe to PATH** if the installer offers it.
- [Node.js](https://nodejs.org/en/download/): version 24 **LTS**.
  This prepares the browser pages and includes `npm`.
- [Git for Windows](https://git-scm.com/install/windows): this downloads the
  project and lets you update it later.

Open the Windows Start menu, type **Command Prompt**, and open it. If it was
already open during installation, close and reopen it.

Run these lines one at a time. Paste a line and press Enter:

```cmd
py -3 --version
node --version
npm --version
git --version
```

Each should print a version number. If a program is "not recognized", check
that it installed and reopen Command Prompt before continuing.

If `py -3` fails but `python --version` shows Python 3.14, replace `py -3`
with `python` in step 3.

### 2. Download the project

In the same Command Prompt:

```cmd
cd /d "%USERPROFILE%"
git clone https://github.com/ColyanM/personal-finance-tracker-public.git
cd personal-finance-tracker-public
```

This puts the project in a folder called `personal-finance-tracker-public`
inside your Windows user folder. The `cd` command changes the folder you are
working in.

If you already have the project, skip the download. Open the folder containing
`app.py` and `README.md` in File Explorer, click the address bar, type `cmd`,
and press Enter. This opens Command Prompt in the right folder.

All remaining commands in this README run from that folder unless a step says
otherwise. In each new Command Prompt, activate the Python environment with
`.venv\Scripts\activate.bat` before running Python commands.

### 3. Set up Python

```cmd
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

This creates and activates a Python environment just for this app, then installs
what it needs. The prompt should show `(.venv)` after activation.

Keep this window open. Wait for each command to finish before running the next
one. If a command fails, fix that step before continuing.

### 4. Create the local database

```cmd
python scripts\db_init.py
python scripts\fx_seed.py seed
python scripts\categories.py seed
python scripts\category_rules.py seed
python scripts\fx_refresh.py latest
```

These create the database, starter categories and rules, and exchange rates.
They do not add any accounts or transactions. You can add categories in the app
later.

The last command gets exchange rates from the public Frankfurter service.
If it fails because you are offline, setup can continue using the saved
fallback rate. That rate may be out of date. Refresh it before importing real
transactions or relying on converted totals: converted transaction amounts are
saved at import time.

Your data is saved in `%LOCALAPPDATA%\FinanceHub`, outside the project folder.

### 5. Build the browser pages

```cmd
cd frontend
npm ci
npm run build
cd ..
```

This may take a few minutes. `npm ci` installs the components listed in the
included lockfile, and `npm run build` prepares the pages the app serves.
You only need to repeat this after updating or changing the app.

### 6. Check the installation and start the app

```cmd
python scripts\security_check.py
python scripts\final_check.py
```

Both should finish with a passed message. Resolve any `FAIL` results before
adding real data. Then run:

```cmd
python app.py
```

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser.
This address opens the app on your own computer.

Keep the Command Prompt window open while using the app. The dashboard will
be empty until you add accounts and transactions.

## Add an account and import transactions

The Accounts page lets you view and rename accounts. Adding a manual account,
changing its balance, and importing a file currently use commands.

Open a second Command Prompt from the project folder while the app is running:
open that folder in File Explorer, type `cmd` in the address bar, and press
Enter. Activate the Python environment:

```cmd
.venv\Scripts\activate.bat
```

Use this second window for the commands below.

### Add a manual account

Replace the example name, bank, currency, and balance with your own:

```cmd
python scripts\accounts.py add --name "Everyday" --institution "Example Bank" --account-type checking --balance-type asset --currency NZD --balance 1000.00
```

- `--name` is an account nickname. You do not need to enter a bank account number.
- `--currency` accepts `NZD` or `USD`.
- `--balance` is in dollars, without a dollar sign or thousands separator.
  `1000.00` means one thousand dollars.
- `--account-type` describes the account: use `checking` for an everyday bank
  account, `credit` for a credit card, `loan` for a loan, or `investment` for
  an investment account.
- Use `--balance-type asset` for money you own. For a credit card or loan,
  use `--balance-type liability` and enter the amount owed as a positive number.

Reload the browser and open **Accounts** to see the account.

Manual balances do not change when you import transactions. To update a balance
later, use the account's exact name:

```cmd
python scripts\accounts.py update --name "Everyday" --balance 950.00
```

### Import transactions from a CSV

A CSV is a spreadsheet saved as plain text. There is no browser button for
adding individual transactions or uploading files yet.

1. Prepare a spreadsheet with the headings below. Bank exports often use
   different headings, so copy the data into this format.
2. Use the exact account name you created. Categories must also match an
   existing category; leave the category blank to use **Uncategorized**.
3. Save as **CSV UTF-8 (comma delimited)**, named `transactions.csv`, in
   `%LOCALAPPDATA%\FinanceHub`. You can paste that folder path into File
   Explorer's address bar to open it.
4. Check the file, then run the backup and import commands below.

This example uses invented data. Importing it adds these rows to your tracker.
Change the dates to the current month if you want to try it in this month's
charts.

```csv
account_name,posted_date,description,amount,currency,category,transaction_id
Everyday,2026-09-01,Example income,1000.00,NZD,Other income,example-income-001
Everyday,2026-09-02,Example grocery shop,-50.00,NZD,Groceries,example-groceries-001
```

Dates use `YYYY-MM-DD`. Money spent is negative; money received is positive.
Leave out currency symbols and thousands separators.

The first five columns are required. `category` and `transaction_id` are
optional. If you include transaction IDs, give each separate transaction a
unique ID and keep it the same in future imports.

```cmd
python scripts\database_backup.py backup
python scripts\import_csv_transactions.py "%LOCALAPPDATA%\FinanceHub\transactions.csv"
```

The generic CSV importer saves immediately. It does not have a preview or
confirmation step. It prints the number of transactions added, matched, or
skipped. Reload the browser and open **Transactions** to check them.

Importing the same IDs again skips existing posted transactions; it does not
edit them. Without IDs, rows with identical account, date, amount, currency,
and description are treated as the same transaction. Use separate IDs for
repeated purchases that happen to have identical details.

Avoid importing the same history through both a file and a bank connection,
as those sources can create duplicates. Keep real CSV files outside the project.

For blank categories, open **Rules** and click **Apply matching rules**, then
review the results. The generic CSV importer does not apply rules automatically.

Separate BNZ and Monarch import scripts are also available. Their requirements
differ from this format; see [Other import formats](#other-import-formats).

## Using the app

Use the sidebar to move between pages, or the menu beside the page title on a
small screen. The currency selector changes displayed totals between NZD and
USD without changing the account's original currency.

### Transactions

1. Open **Transactions** and select **Needs review**.
2. Use the search, account, category, or date filters to find entries.
3. Choose a category from the row's category menu. Changes save immediately.
4. Click **Mark reviewed** when you have checked the entry.
5. For a purchase covering several categories, click **Split**, enter the
   amounts as positive numbers, and click **Save split**. They must add up to
   the original transaction amount, ignoring its minus sign.

**Pending** transactions have not been finalized by the bank. **Posted**
transactions have. A pending amount can change when it posts.

Use transfer categories for money moved between your own accounts so it does
not count as everyday income or spending.

### Budget

The Budget page opens on the current month. Use **Previous month**, **Current
month**, or **Next month** to change it.

1. Click **Show … unbudgeted** beneath a group to reveal unused categories.
   On a new setup, most categories are hidden until you do this.
2. Enter expected income in **Planned $** beside an Income category such as
   **Primary paycheck**. Click outside the field to save it.
3. Enter spending allowances beside expense categories such as **Groceries**.
   Use positive amounts.
4. Once planned income is set, you can use **Planned %** instead of dollars.
   For example, `10` means 10% of that month's planned income.
5. Click outside each field and wait for the update to finish. There is no
   separate Save button for budget rows.

**Actual** shows recorded income or spending, and **Remaining** shows what is
left against the plan. **Rollover** carries eligible unused budget forward.
Each group also shows its combined totals.

To add a category, open **Add a budget category**, enter its name, group, and
type, then click **Add category**.

### Rules

Rules save you from categorizing the same merchant repeatedly.

Open **Rules**, enter text in **Merchant contains**, choose a category, and
click **Save rule**. Leave priority at `100` for a simple rule; lower numbers
run first when several rules could match.

Click **Apply matching rules** to apply them to Uncategorized transactions.
Already categorized entries stay as they are. **Turn off** disables a saved rule.

### Other pages

| Page | Use |
| --- | --- |
| **Dashboard** | Account totals, monthly income and spending, budget progress, and recent transactions. |
| **Accounts** | Balances and net worth: what you own minus what you owe. Click an account name to rename it. |
| **Cash Flow** | Income and expenses over the selected period. |
| **Reports** | Spending by category and comparisons across time periods. |
| **Recurring** | Estimates of future payments and income based on transaction history. |
| **Goals** | Savings targets. Enter and update the saved amount yourself. |
| **Investments** | View or enter holdings. Create an account with `--account-type investment` first so it appears in the account selector. |
| **Sync** | Refresh results and checks for missing or duplicate data. |
| **Settings** | Display currency and optional email-alert settings. |

Use the timeline selectors to see older history. If a page is empty, check the
selected dates and filters.

**Refresh all** updates configured bank connections. Providers you have not
set up are skipped. It does not import CSV files or update manual balances.

## Starting and stopping the app

You only need to install the app once. To open it another day:

1. Open the project folder in File Explorer.
2. Type `cmd` in the address bar and press Enter.
3. Run:

```cmd
.venv\Scripts\activate.bat
python app.py
```

4. Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser.

To stop it, click the Command Prompt window running the app and press
**Ctrl+C**. Closing the browser does not stop the app. Saved data stays there
for next time.

## Backups

Create a backup before imports, updates, or other changes you might need to
undo. In a Command Prompt with `.venv` activated:

```cmd
python scripts\database_backup.py backup
python scripts\database_backup.py list
```

Backups are saved in `%LOCALAPPDATA%\FinanceHub\backups`. They contain private
financial information. Keep any extra copies somewhere private and protected;
a backup on the same computer will not help if the computer is lost.

Stop the app before restoring. If you enabled scheduled app or refresh tasks,
stop those too. Replace `BACKUP_FILE_NAME` below with the exact filename shown
by the list command:

```cmd
python scripts\database_backup.py restore BACKUP_FILE_NAME --confirm RESTORE
```

This replaces the current database with the saved version and creates a safety
backup of the current database first. Start the app again afterward.

A database backup includes your accounts, transactions, budgets, and other
database records. It does not include bank/email credentials, raw imports, or
scheduled-task configuration. Connections need setting up again if you move
to another Windows user or computer.

## Other import formats

For a spreadsheet you prepare yourself, use the
[generic CSV walkthrough](#import-transactions-from-a-csv).
The specialized importers below expect their own export formats.

Create a backup before importing and keep the files outside the project:

```cmd
python scripts\database_backup.py backup
python scripts\bnz_csv_import.py --help
python scripts\monarch_import.py --help
```

The BNZ importer expects BNZ transaction CSVs with `dd/mm/yy` dates.
Filenames must contain `everyday` or `savings`. It also requires both active
accounts named `Everyday` and `Savings` under provider `akahu-bnz`, even if only
one file is imported. It is intended for history alongside that bank setup.
Use its `preview` command first; saving uses `import` with `--confirm IMPORT`.

The Monarch importer accepts balance exports with `Date,Balance,Account`
columns and transaction exports with `Date,Merchant,Category,Account,Amount`
columns. Choose `--balances`, `--transactions`, or both, and set `--currency`
to `NZD` or `USD`. Run `preview` before `import --confirm IMPORT`.

Preview output and import errors can contain account names or other private
details. Review them locally. Avoid overlapping file imports and live bank
history, and check Transactions and Sync after importing.

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

Both bank providers plus email (Akahu and Plaid must both be configured):

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

## Choose a private data folder

The default data folder is `%LOCALAPPDATA%\FinanceHub`. To use another location,
set `FINANCE_HUB_DATA_DIR` before initializing the database or starting the app.
The chosen folder must be outside the project and outside OneDrive.

Use the same setting for every command and scheduled task. Changing it points
the app at another folder; it does not move existing data. Back up the database
before moving it, and keep credentials and other private files protected.

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

The commands below use the default data folder. If you set
`FINANCE_HUB_DATA_DIR`, use the database in that folder instead.
Remove the private database and initialize it again:

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

## Updating or developing the app

Stop the app and any scheduled refreshes before updating. Create a backup, then
download the source updates, install dependencies, migrate the database, and
rebuild:

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

### A command cannot find the app or its files

Open the folder containing `app.py` in File Explorer, type `cmd` in its address
bar, and press Enter. Run `.venv\Scripts\activate.bat` before Python commands.

This guide uses Command Prompt. If PowerShell blocks an activation script,
switch to Command Prompt and use the commands shown here.

### The browser cannot connect

Check that `python app.py` is still running and use exactly
`http://127.0.0.1:8000`. Look at the command window for an error.

If port 8000 is already in use, check for another running copy of Finance Hub.
Use that copy or stop it with Ctrl+C before starting another.

### The frontend is missing or the browser shows old pages

Repeat [step 5](#5-build-the-browser-pages), then reload the browser.
Restart `python app.py` too if you updated the Python code.

### A timezone or tzdata error appears

Activate `.venv` and reinstall the requirements:

```cmd
python -m pip install -r requirements.txt
python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Pacific/Auckland'))"
```

The last command should print `Pacific/Auckland`.

### An import fails or the numbers look wrong

Check the CSV headings, comma delimiters, dates, and account/category names.
In Transactions, check the selected dates and filters and the import's
added/skipped counts. Manual account balances need updating separately.

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

When asking for help, include the step and error message with private details
removed. Do not attach bank exports, databases, passwords, or tokens.
Report security issues through [SECURITY.md](SECURITY.md).

## Privacy

Private files are stored outside the project, by default in:

```text
%LOCALAPPDATA%\FinanceHub
```

Paste that path into File Explorer to find the database, backups, exports, and
any local connection settings or logs. Keep those files outside Git and
OneDrive.

Bank tokens and email passwords use Windows user-profile encryption. The
database, exports, and backups are not encrypted by the app, so protect access
to your Windows account and any copies of those files.

The web app listens only on `127.0.0.1` and has no separate app login. It
contacts a public service for exchange rates at startup. Optional bank
connections communicate with Akahu or Plaid; optional email alerts send
financial summaries through Yahoo to the recipients you configure. Bank
connections request read-only access.

Do not put real transactions, account details, email addresses, tokens, or
screenshots containing private information in GitHub issues or commits.
The ignore rules help keep generated files out of Git. The local security
check checks configuration, known token values and recognizable credential
formats in working files, and private or generated filenames in the Git index
when Git is available. It does not scan staged source contents or Git history,
and it cannot detect every kind of personal information. Review staged changes
before committing, even when the checks pass.

## Security notes

- The web app listens only on `127.0.0.1`.
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
