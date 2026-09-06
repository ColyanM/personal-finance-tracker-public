# Security Policy

## Supported version

Security fixes are made on the latest version of the default branch. Please
check that the issue still exists there before reporting it.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability. Use GitHub's private
vulnerability reporting flow:

1. Open the repository's **Security** tab.
2. Select **Advisories**.
3. Select **Report a vulnerability**.

Include the affected component, impact, reproduction steps, and a suggested fix
when available. Use invented accounts, identifiers, amounts, and transactions in
every example. Do not test against financial accounts or third-party services
that you do not own or have explicit permission to test.

If private vulnerability reporting is unavailable, open a minimal public issue
asking the maintainer to provide a private reporting route. Do not include the
vulnerability details in that issue.

## Never include sensitive data

Do not attach or paste real financial information or secrets into a report,
issue, discussion, pull request, commit, screenshot, or log. This includes:

- Bank, card, investment, or loan statements and transaction exports.
- Account, transaction, customer, institution, or Plaid Item identifiers.
- API keys, access tokens, passwords, authorization headers, or recovery codes.
- Email addresses, Tailscale hostnames or logins, private URLs, or personal
  names.
- SQLite databases, backups, application-data folders, configuration files, or
  logs produced while using real accounts.

Redact sensitive values and replace them with clearly synthetic placeholders.
If sensitive material is accidentally published, revoke or rotate affected
credentials first, then report the exposure privately.

## Public security discussions

After a fix is available, maintainers may publish a GitHub security advisory
with appropriate credit. Please allow time for investigation and remediation
before disclosing details publicly.
