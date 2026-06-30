# upbain

Single-file Python application: `tool__tauto_nostage.py` (v25) — a Telegram **userbot**
(runs under a real Telegram user account, not a bot-API bot) built with Pyrogram. It
automates forwarding posts from the account's *Saved Messages* into target channels with
topic-aware routing, anti-flood rate limiting, and folder/channel auto-sync. There is no
web server, database, or test suite; all state is local JSON files plus a Pyrogram
`.session` (SQLite) file created on first login.

## Cursor Cloud specific instructions

### Services
- One process only: `python3 tool__tauto_nostage.py` (entry point `app.run(main())`).
- No local backing services. The only external dependency is **Telegram's MTProto API**,
  reached over the network with valid credentials and an authenticated user session.

### Required configuration (read at import time)
`load_dotenv()` runs at module import and the script immediately does
`int(os.getenv("API_ID"))` etc., so the process **crashes on import** unless a `.env`
file (or equivalent env vars) is present in the working directory with all four of:
- `API_ID` (int, from my.telegram.org)
- `API_HASH` (str)
- `INTERMEDIATE_CHAT` (int, e.g. `-100...`)
- `ADS_CHAT` (int)

No `.env.example` is committed. There is intentionally no `.env` in the repo.

### Running / testing caveats (important, non-obvious)
- **First run is interactive**: with no `test_session.session` present, Pyrogram prompts
  on stdin for phone number, then the login code (and 2FA password if set). In a
  non-interactive shell this raises `EOFError`. End-to-end runs therefore require a real,
  logged-in Telegram account; this cannot be fully automated in the cloud VM because the
  login OTP is delivered to the account owner's phone.
- This is a **userbot operating a real account** — exercising forwarding carries
  account-flood/ban risk. Prefer a throwaway test account and test channels. The code has
  extensive anti-flood logic (TokenBucket, global flood gate, retries) for this reason.
- To smoke-test that the environment is healthy without an account, run the script with
  placeholder credentials and no stdin; a correct setup prints the `[CONFIG]` line,
  initializes Pyrogram, connects to Telegram, and reaches the
  `Enter phone number or bot token:` prompt before erroring on `EOFError`.
- Running the script writes state files into the current working directory
  (`channels.json`, `folders.json`, `failed_msgs.json`, `topic_rr.json`, `topic_map.txt`,
  `test_session.session`). Run smoke tests in a scratch dir to keep the repo clean.

### Lint / build
- No linter or build step is configured. `python3 -m py_compile tool__tauto_nostage.py`
  is a quick syntax check.

### System dependency note
- `tgcrypto` is a C extension; it needs `python3-dev` + `build-essential` to compile.
  These are already provisioned in the VM snapshot, so `pip install -r requirements.txt`
  works out of the box.
