#!/bin/bash
# Wrapper for volume_velocity_scanner.py
# 让 launchd 也能读到 ~/.cresus/env.sh 里的 secrets (TG token 等)
# 不把 secret 写进 plist 或 repo, 避免 apply 覆盖.

# Load secrets if user has configured them (silently skip if absent)
if [ -f "$HOME/.cresus/env.sh" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.cresus/env.sh"
fi

exec /usr/bin/python3 "$HOME/cresus-bot/scripts/volume_velocity_scanner.py" "$@"
