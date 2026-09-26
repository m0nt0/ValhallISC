# ValhallISC command line

Without a command, `valhallisc` starts the tray app. With a command it runs headless. Add `--batch` to disable prompts and get one JSON object on stdout (with `"ok": true|false`).

```sh
# profiles
echo "$PW" | valhallisc --batch --create-profile Dev --host iris.example.com --user _SYSTEM --password-stdin
valhallisc --batch --create-profile Prod --host iris.example.com --port 443 --https --prefix /iris \
           --user deploy --mountpoint ~/IRIS/prod --read-only --password-stdin < pw.txt
valhallisc --batch --update-profile Dev --rename Development --no-compile
valhallisc --batch --list-profiles
valhallisc --batch --show-profile Dev
valhallisc --batch --delete-profile Dev

# connections
valhallisc --batch --test Dev          # login check, lists namespaces
valhallisc --batch --connect Dev       # mounts in the background, returns when mounted
valhallisc --batch --status            # which profiles are connected (by the CLI or the tray app)
valhallisc --batch --disconnect Dev    # add --force if the folder is in use
```

Every `--flag NAME` has a sub-command form: `profile create|update|delete|show|list`, `test`, `connect`, `disconnect`, `status`. Run `valhallisc <command> --help` for all options. Profile options: `--host --port --user --mountpoint --password-stdin --password --[no-]read-only --[no-]https --[no-]verify-tls --prefix --[no-]show-system --[no-]compile --compile-flags`.

The password is taken from `--password-stdin`, the `VALHALLISC_PASSWORD` environment variable, `--password` (other local users can see it), or an interactive prompt. It's stored in the OS keyring.

| Exit code | Meaning |
|---|---|
| 0 | ok (also: `connect` when already connected, `disconnect` when not connected) |
| 1 | generic error |
| 2 | bad arguments or invalid profile values (`fields` in the JSON lists them) |
| 3 | login failed or server unreachable |
| 4 | mount failed (no FUSE driver, bad folder, timeout) |
| 5 | unknown profile |
| 6 | folder in use, or the profile is connected when it must not be |

Mounts made with `connect` and by the tray app share a registry, so each side sees, and can disconnect, the other's mounts.

When no FUSE driver is usable, `connect` fails with exit code 4 and `valhallisc doctor` prints install instructions tailored to the machine (package manager and distribution).

On macOS the CLI is `dist/valhallisc` (a wrapper around `ValhallISC.app`). On Windows it's `valhallisc-cli.exe`.
