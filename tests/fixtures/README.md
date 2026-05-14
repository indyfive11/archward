# Test fixtures

Sample outputs captured from a real run on an Arch-based machine. Used by the
unit tests as parse-format baselines so format drift in pacman / checkupdates
/ yay surfaces as a test failure rather than a silent regression in
production.

## Regenerate

When pacman / yay output format changes (e.g. a pacman major release):

```bash
# pacman queries — capture, sanitize, commit
pacman -Qe | head -50 > tests/fixtures/pacman_output/explicit-sample.txt
pacman -Qm                 > tests/fixtures/pacman_output/foreign-sample.txt

# pending updates
checkupdates > tests/fixtures/checkupdates_output/pending-sample.txt
yay -Qua     > tests/fixtures/yay_qua_output/pending-sample.txt

# config — capture and replace /home/<user>/ with /home/USER/
cp ~/.config/archward/config.toml tests/fixtures/config_samples/default-after-detect.toml
sed -i "s|/home/$USER/|/home/USER/|g" tests/fixtures/config_samples/default-after-detect.toml
```

After regenerating, run `pytest tests/unit/ -q` to confirm the parsers still
agree with the new shape, then commit.

## What's in each file

| Path | Source | Notes |
|---|---|---|
| `pacman_output/explicit-sample.txt` | `pacman -Qe` | First 50 explicit packages on a real desktop install. |
| `pacman_output/foreign-sample.txt` | `pacman -Qm` | Installed AUR / foreign packages — used to validate the no-helper-detected fallback. |
| `checkupdates_output/pending-sample.txt` | `checkupdates` | Format: `pkg old_version -> new_version` lines. |
| `yay_qua_output/pending-sample.txt` | `yay -Qua` | Same format as checkupdates. |
| `config_samples/default-after-detect.toml` | `~/.config/archward/config.toml` after `archward --detect` on EndeavourOS + yay | Path sanitized: `/home/$USER/` → `/home/USER/`. |
