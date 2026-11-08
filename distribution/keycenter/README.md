# Key center (SOPS + age; the encrypted repository lives on Tokyo)

You collect secrets by hand, from wherever they are now (env files, vault-style
folders, notes). The key center keeps them: encrypted at rest, delivered only to
the device that needs each one, rotated in one place. Agents (GPT, Claude) only
ever see variable **names**.

```
recipients.yaml                              owner + one age public key per device (public, not secret)
.sops.yaml                                   generated: serveros_secret.py recipients --write
vault/<bundle>.sops.yaml                     the owner's store: everything you collected (owner-only)
devices/<id>/<bundle>.sops.yaml              what one device gets (owner + that device)
devices/<id>/<bundle>.target.yaml            env_file + units to restart (plain)
```

Where it lives: a bare Git repository **on Tokyo** (no GitHub), e.g.
`/data/home/vince/serveros-secrets.git`. It holds only ciphertext; Tokyo can
decrypt only bundles addressed to Tokyo. Devices pull it over SSH (the A6000
already has an SSH link to Tokyo). Back that one directory up.

## One-time setup

1. **You (your machine):** install `sops` (≥ 3.9) and `age`;
   `age-keygen -o ~/.config/serveros/owner.age.key`. Back the file up offline —
   it is the one key that opens the vault. The printed `age1…` line is public.
2. **Local agent, on Tokyo:** `git init --bare ~/serveros-secrets.git`; clone it;
   write `recipients.yaml` with your public key and each device's (step 3);
   `python3 distribution/keycenter/serveros_secret.py recipients --repo <clone> --write`; commit; push.
3. **Local agent, on each device:** install `sops` and `age`;
   `serveros_secret.py keygen` (prints the device's public key for step 2);
   clone the Tokyo repository (`git clone vince@<tokyo>:serveros-secrets.git ~/serveros-secrets`
   from the A6000, the path itself on Tokyo); write `~/.config/serveros/secrets.yaml`:
   ```yaml
   repo: ~/serveros-secrets
   device: device:llm-lab-dell
   ```
   `device_kit.py update --device <name> … --enable` installs the 5-minute sync timer.
4. **Per device bundle:** `devices/<id>/cloudcli.target.yaml`, e.g. on the A6000
   ```yaml
   env_file: ~/.config/serveros/cloudcli.env
   restart: [cloudcli.service]
   ```
   and point CloudCLI's unit at it (`EnvironmentFile=%h/.config/serveros/cloudcli.env`).

## Taking in what you collected (you, on your machine)

Put everything you gathered in one place — any mix of:

- `.env` files (`NAME=value`, `export NAME="value"`),
- `.yaml` / `.json` mappings,
- vault-style folders: one file per secret, the file name is the name
  (`gw0-token` becomes `GW0_TOKEN`).

Then, with your owner clone of the repository:

```bash
export SOPS_AGE_KEY_FILE=~/.config/serveros/owner.age.key
python3 distribution/keycenter/serveros_secret.py import ~/collected --repo ~/serveros-secrets --shred
git -C ~/serveros-secrets push
```

Add `--per-file` when you collected several files that may share names (a
test env's `LITELLM_MASTER_KEY` beside production's): each file becomes its own
bundle, `vault/<file-name>`, so nothing is merged. It prints only names: `added`, `replaced`, `unchanged`, and what it `skipped`
(nested values, unreadable lines, conflicting duplicates), never a value.
`--shred` overwrites and deletes the collected files once they are encrypted
(best effort on SSDs — keep the collection on a disk you trust, or delete it
yourself). Without `--device` everything goes to `vault/main`, which only you
can open.

Give a device what it needs from the vault:

```bash
python3 distribution/keycenter/serveros_secret.py assign GW0_TOKEN --device device:llm-lab-dell --repo ~/serveros-secrets
git -C ~/serveros-secrets push
```

The device applies it within five minutes and restarts CloudCLI only if a value
changed. See what is where, names only, no key needed:

```bash
python3 distribution/keycenter/serveros_secret.py inventory --repo ~/serveros-secrets
```

## Every day

- Rotate one value: `serveros_secret.py set GW0_TOKEN --device device:llm-lab-dell --repo …`
  (value read without echo), push.
- Revoke a device: remove it from `recipients.yaml`, `recipients --write`,
  `sops updatekeys` its bundles, **and rotate every value it could read** (old
  ciphertexts stay in Git history).
- An agent adds a model entry naming the variable (`envref`);
  `fleet_models_list` shows which names a device holds (`variables`).

Moving to GitHub or OpenBao later changes the remote, or `decrypt_bundle` /
`encrypt_bundle` — nothing that reads the env files.
