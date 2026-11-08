# Key center (SOPS + age, Git as the authority)

Secrets live encrypted in the private repository **ProfessorZhi/serveros-secrets**.
Each bundle is encrypted to the owner and to the one device that uses it. A
device's age private key never leaves it; the owner's never leaves the owner's
machine. Agents (GPT, Claude) only ever see variable **names**.

```
recipients.yaml                              owner + one age public key per device
.sops.yaml                                   generated: serveros-secret recipients --write
devices/<id>/<bundle>.sops.yaml              NAME: value, encrypted
devices/<id>/<bundle>.target.yaml            env_file + units to restart (plain)
```

## One-time setup

1. **Owner (your machine):** `age-keygen -o ~/.config/serveros/owner.age.key`;
   keep it, back it up offline. Its public key (`age1…`) goes in `recipients.yaml`.
2. **Repository:** create private `ProfessorZhi/serveros-secrets`, clone it, write
   `recipients.yaml`:
   ```yaml
   owner: age1...
   devices:
     device:llm-lab-dell: age1...
     device:tokyo-shared-dev-01: age1...
   ```
   then `python3 distribution/keycenter/serveros_secret.py recipients --repo <clone> --write`.
3. **Each device (local agent):** install `sops` (≥ 3.9) and `age`; run
   `serveros_secret.py keygen` and send the printed public key to the owner; clone
   the secrets repo; write `~/.config/serveros/secrets.yaml`:
   ```yaml
   repo: ~/serveros-secrets
   device: device:llm-lab-dell
   ```
   `device_kit.py update --device <name> … --enable` installs the 5-minute sync timer.
4. **Per bundle:** add `devices/<id>/cloudcli.target.yaml`, e.g. for the A6000:
   ```yaml
   env_file: ~/.config/serveros/cloudcli.env
   restart: [cloudcli.service]
   ```
   and point the CloudCLI unit at it (`EnvironmentFile=%h/.config/serveros/cloudcli.env`).

## Every day

- Add or rotate a key (owner, at the keyboard; the value is not echoed and goes to
  sops through a pipe): `serveros_secret.py set GW0_TOKEN --device device:llm-lab-dell --repo <clone>`, then `git push`.
  The device applies it within five minutes and restarts CloudCLI only if it changed.
- Revoke a device: remove it from `recipients.yaml`, `recipients --write`,
  `sops updatekeys` each of its bundles, **and rotate every value it could read**
  (old ciphertexts stay in Git history).
- An agent adds a model entry naming the variable (`envref`); `fleet_models_list`
  shows which names each device holds (`variables`), never values.

Moving to OpenBao later replaces `decrypt_bundle` / `encrypt_bundle` in
`serveros_secret.py`; the env files, CloudCLI, Fleet and the agents do not change.
