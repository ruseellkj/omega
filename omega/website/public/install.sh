#!/bin/sh
#
# omega installer.  curl -fsSL https://omega-coding-agent.vercel.app/install.sh | sh
#
# Shaped after Tau's (research/tau/website/static/install.sh, 72 lines), which
# gets three things right and is worth copying rather than improvising:
#
#   1. It bootstraps `uv` rather than assuming Python.  omega needs 3.14, which
#      is newer than most systems ship, and uv fetches an interpreter itself.
#   2. It never edits a shell rc file.  uv owns PATH; the script only *reports*
#      when the bin directory is not on it.  A tool that silently appends to
#      ~/.zshrc is a tool you cannot cleanly uninstall.
#   3. It verifies the shim it just installed before claiming success, so a
#      broken install fails here rather than the first time you run it.
#
# `set -eu` and POSIX sh throughout: this is piped into whatever /bin/sh is, and
# that is not necessarily bash.

set -eu

UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

# **Where omega comes from.**  The PyPI name is `omega-coding-agent` (`omega` is
# taken by an unrelated v0.4.0 games library) and the command is still `omega`.
#
# This was `git+https://github.com/ruseellkj/omega` until v0.1.0 was published on
# 2026-09-25 — the one line that was always going to change after the first
# release.  Installing from the index downloads a wheel instead of cloning and
# building on the machine, so it is faster and needs no build toolchain, and
# `uv tool upgrade` starts resolving properly rather than re-cloning.
#
# To install an unreleased main, or a fork, or a branch:
#
#     OMEGA_SOURCE="git+https://github.com/ruseellkj/omega#subdirectory=omega" sh install.sh
OMEGA_SOURCE="${OMEGA_SOURCE:-omega-coding-agent}"

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return
    fi

    if [ -n "${UV_INSTALL_DIR:-}" ] && [ -x "$UV_INSTALL_DIR/uv" ]; then
        printf '%s\n' "$UV_INSTALL_DIR/uv"
        return
    fi
    if [ -n "${XDG_BIN_HOME:-}" ] && [ -x "$XDG_BIN_HOME/uv" ]; then
        printf '%s\n' "$XDG_BIN_HOME/uv"
        return
    fi

    # A uv installed a moment ago is not on PATH in this shell yet, so the
    # places its own installer puts it are checked directly.
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return
        fi
    done

    return 1
}

uv_bin=$(find_uv || true)
if [ -z "$uv_bin" ]; then
    printf '%s\n' "omega runs in an isolated environment managed by uv."
    printf '%s\n' "uv was not found, so Astral's official installer will run now."

    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "$UV_INSTALLER_URL" | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$UV_INSTALLER_URL" | sh
    else
        printf '%s\n' "Error: installing uv needs curl or wget, and neither is here." >&2
        exit 1
    fi

    uv_bin=$(find_uv || true)
    if [ -z "$uv_bin" ]; then
        printf '%s\n' "Error: uv installed but its executable could not be found." >&2
        printf '%s\n' "Open a new terminal and run: uv tool install $OMEGA_SOURCE" >&2
        exit 1
    fi
fi

printf '%s\n' "Installing omega from $OMEGA_SOURCE ..."
"$uv_bin" tool install --force "$OMEGA_SOURCE"

# Verify before claiming anything.  `uv tool dir --bin` is asked rather than
# guessed, because it honours UV_TOOL_BIN_DIR and XDG layouts.
#
# **NO_COLOR is load-bearing, not tidiness.**  uv writes this path with ANSI
# colour codes wrapped around it *even through a pipe* — verified with `od -c`.
# Without this the variable holds "\033[36m/path\033[39m", every subsequent
# test on it fails, and the script reports a missing shim it had just installed.
tool_bin=$(NO_COLOR=1 "$uv_bin" tool dir --bin)
omega_bin="$tool_bin/omega"
if [ ! -x "$omega_bin" ]; then
    printf '%s\n' "Error: installed, but $omega_bin is missing." >&2
    exit 1
fi

printf '%s\n' ""
"$omega_bin" --version
printf '%s\n' ""
printf '%s\n' "omega is installed.  Run:  omega"
printf '%s\n' "Then /login to sign in - browser or API key - or export ANTHROPIC_API_KEY."

case ":$PATH:" in
    *":$tool_bin:"*) ;;
    *)
        printf '%s\n' ""
        printf '%s\n' "Note: $tool_bin is not on your PATH."
        printf '%s\n' "Restart your shell, or run: uv tool update-shell"
        ;;
esac
