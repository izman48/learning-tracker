# Security policy

## Reporting a vulnerability

Please report it privately through GitHub: on this repository, open **Security → Report a vulnerability**. Please do not open a public issue.

Include what you found, how to reproduce it, and the impact you expect. We aim to reply within 7 days.

## Scope

In scope: the code in this repository and the default configuration it ships (Docker Compose, `.env.example`).

Out of scope:
- a self-hoster's own server, proxy or mail settings;
- third-party services the app calls (AI provider, search, Hacker News).

## Defaults we promise

- The app fails closed. A missing secret or key stops startup.
- The invite gate is on by default.
- There is no default admin account.
- The database is not published.
