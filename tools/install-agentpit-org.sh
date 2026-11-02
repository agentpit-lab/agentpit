#!/bin/sh
# Installs or updates PaceLine, PitBoard and PitCrew as organization (managed) plugins on macOS and Linux.
#
# On a Team or Enterprise login, and on any machine with managed settings, the built-in
# sec-default guard skips the hooks of plugins a person installs. A plugin counts as the
# organization's when managed settings enable it from a directory marketplace at an absolute,
# root-only path that lists it by a relative source. This script fetches each plugin from
# github.com/agentpit-lab/agentpit at its release tag (<plugin>--v<version>, the latest
# unless --version names one) and mirrors it there, writes a managed-settings.d drop-in
# (an existing managed-settings.json is left untouched), carries the plugin data to the new
# id and disables the user-scope copies.
#
# Run it as yourself, not with sudo: it calls sudo for the system paths and needs your
# home for the rest. Run it again to update: a rerun keeps every plugin already seated, and
# removes one only when you ask (--remove, or a yes at the question --plugins can raise). Where the organization has server-managed
# settings, the admin console must carry "managedSourcesBehavior": "merge".
# Needs git unless --source is given.
#
# Usage: install-agentpit-org.sh [--plugins <name>,<name>...] [--remove <name>,<name>...]
#                                [--version <plugin>=<version>]... [--latest]
#                                [--source <marketplace dir>] [--skip-user-plugins] [--uninstall]
#   --plugins  the plugins to seat, from paceline, pitboard, pitcrew and stint, comma-separated:
#              --plugins paceline,pitboard; the default is paceline,pitboard,pitcrew (Stint's
#              module needs no seating). A plugin already seated and left out is kept, or asked
#              about in a terminal, never removed silently
#   --remove   removes seated plugins and gives you your own copy back: --remove stint; it may
#              repeat. Naming a plugin in --remove and in --plugins or --version is an error
#   --version  pins a plugin to a release: --version pitboard=0.1.14
#   --latest   takes the latest release of every plugin not pinned, without asking anything,
#              the removal question included (seated plugins left out are kept)
#   --source   a local marketplace folder with plugins/<name>, used instead of GitHub
# A run in a terminal offers each plugin's last ten releases with the latest first, through
# fzf when it is installed and a numbered list otherwise; Enter keeps the latest. A run
# without a terminal (cron, launchd, redirected input) takes the latest.
set -eu

marketplace=agentpit-org
user_marketplace=agentpit
repository=https://github.com/agentpit-lab/agentpit
# The known plugin list also appears in Install-AgentpitOrg.ps1 (two ValidateSets and $known); keep them in step.
known="paceline pitboard pitcrew stint"
# The default list also appears in Install-AgentpitOrg.ps1 (the -Plugins default); keep them in step.
plugins="paceline pitboard pitcrew"
plugins_given=0
remove=
pins=
latest=0
source_dir=
skip_user=0
uninstall=0

while [ $# -gt 0 ]; do
    case $1 in
        --plugins|--remove|--version|--source) [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; } ;;
    esac
    case $1 in
        --plugins)
            plugins=; plugins_given=1
            for name in $(printf '%s' "$2" | tr ',' ' '); do
                case " $known " in *" $name "*) ;; *) echo "--plugins takes one or more of: $known" >&2; exit 2 ;; esac
                case " $plugins " in *" $name "*) ;; *) plugins="$plugins${plugins:+ }$name" ;; esac
            done
            [ -n "$plugins" ] || { echo "--plugins takes one or more of: $known" >&2; exit 2; }
            shift 2 ;;
        --remove)
            list=
            for name in $(printf '%s' "$2" | tr ',' ' '); do
                case " $known " in *" $name "*) ;; *) echo "--remove takes one or more of: $known" >&2; exit 2 ;; esac
                case " $list " in *" $name "*) ;; *) list="$list${list:+ }$name" ;; esac
            done
            [ -n "$list" ] || { echo "--remove takes one or more of: $known" >&2; exit 2; }
            for name in $list; do
                case " $remove " in *" $name "*) ;; *) remove="$remove${remove:+ }$name" ;; esac
            done
            shift 2 ;;
        --version)
            [ "${2%%=*}" != "$2" ] || { echo "--version takes <plugin>=<version>" >&2; exit 2; }
            pins="$pins $2"; shift 2 ;;
        --latest) latest=1; shift ;;
        --source) source_dir=$2; shift 2 ;;
        --skip-user-plugins) skip_user=1; shift ;;
        --uninstall) uninstall=1; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

has() {  # $1: a name, $2: a space-separated list -> 0 when the list holds it
    case " $2 " in *" $1 "*) return 0 ;; esac
    return 1
}

known_order() {  # $1: names -> those names in known order
    ordered=
    for k in $known; do
        if has "$k" "$1"; then ordered="$ordered${ordered:+ }$k"; fi
    done
    echo "$ordered"
}

join_names() {  # $1: space-separated names -> a comma-and-space list
    printf '%s' "$1" | sed 's/ /, /g'
}

if [ "$plugins_given" -eq 1 ]; then
    clash=
    for name in $remove; do
        if has "$name" "$plugins"; then clash="$clash${clash:+ }$name"; fi
    done
    [ -z "$clash" ] || { echo "--remove names a plugin that --plugins seats: $(join_names "$(known_order "$clash")")" >&2; exit 2; }
fi
clash=
for pin in $pins; do
    if has "${pin%%=*}" "$remove"; then clash="$clash${clash:+ }${pin%%=*}"; fi
done
[ -z "$clash" ] || { echo "--remove names a plugin that --version pins: $(join_names "$(known_order "$clash")")" >&2; exit 2; }

if [ "$(id -u)" -eq 0 ]; then
    echo "Run as your own user; the script calls sudo where it needs it." >&2
    exit 2
fi

case $(uname -s) in
    Darwin)
        root="/Library/Application Support/ClaudeCode"
        target="$root/plugins/$marketplace"
        group=wheel ;;
    Linux)
        root=/etc/claude-code
        target=/opt/claude-code/plugins/$marketplace
        group=root ;;
    *) echo "unsupported OS: $(uname -s)" >&2; exit 2 ;;
esac
drop_in="$root/managed-settings.d/50-$marketplace.json"
data_root="$HOME/.claude/plugins/data"

set_user_plugins() {  # $1: enable | disable, $2: the plugins, $plugins by default
    [ "$skip_user" -eq 1 ] && return 0
    if ! command -v claude >/dev/null 2>&1; then
        echo "warning: claude is not on PATH; $1 the @$user_marketplace copies by hand." >&2
        return 0
    fi
    for p in ${2-$plugins}; do
        claude plugin "$1" "$p@$user_marketplace" --scope user >/dev/null 2>&1 \
            && echo "User-scope $p@$user_marketplace: $1d." || true
    done
}

if [ "$uninstall" -eq 1 ]; then
    sudo rm -f "$drop_in"
    sudo rm -rf "$target"
    set_user_plugins enable
    echo "Removed $drop_in and $target. Restart Claude Code sessions to apply."
    exit 0
fi

# The plugins already seated are the ones the existing drop-in enables, read by pattern, line
# by line: the file is this script's own output (or the PowerShell installer's), which puts
# each key and its true on one line.
seated=
if [ -e "$drop_in" ] || [ -L "$drop_in" ]; then
    # Read through sudo: the file may be root-only. LC_ALL=C keeps [a-z] from matching capitals.
    # </dev/null keeps sudo (use_pty) from swallowing an answer typed ahead of the removal prompt.
    sudo test -f "$drop_in" </dev/null || { echo "Cannot read $drop_in; fix or delete it, or run with --uninstall." >&2; exit 1; }
    content=$(sudo cat "$drop_in" </dev/null) \
        || { echo "Cannot read $drop_in; fix or delete it, or run with --uninstall." >&2; exit 1; }
    found=$(printf '%s\n' "$content" | LC_ALL=C grep -o '"[a-z0-9-][a-z0-9-]*@agentpit-org"[[:space:]]*:[[:space:]]*true') || [ $? -eq 1 ] \
        || { echo "Cannot read $drop_in; fix or delete it, or run with --uninstall." >&2; exit 1; }
    unknown=
    for name in $(printf '%s\n' "$found" | LC_ALL=C sed 's/^"\([a-z0-9-]*\)@.*/\1/'); do
        if has "$name" "$known"; then
            if ! has "$name" "$seated"; then seated="$seated${seated:+ }$name"; fi
        elif ! has "$name" "$unknown"; then
            unknown="$unknown${unknown:+ }$name"
        fi
    done
    [ -z "$unknown" ] || { echo "$drop_in seats plugins this installer does not know: $(join_names "$unknown"); fix or delete it, or run with --uninstall." >&2; exit 1; }
    seated=$(known_order "$seated")
fi

# Requested: the plugins asked for (--plugins or the default) minus --remove, plus the pinned
# seated ones. Extra: seated, not requested, not removed. The final set is requested plus the
# extra the run keeps; it replaces $plugins from here on.
base=
for name in $plugins; do
    if ! has "$name" "$remove"; then base="$base${base:+ }$name"; fi
done
carry=
for name in $seated; do
    if ! has "$name" "$base" && ! has "$name" "$remove"; then carry="$carry${carry:+ }$name"; fi
done
allowed="$base${carry:+${base:+ }$carry}"
pinned=
for pin in $pins; do
    has "${pin%%=*}" "$allowed" || { echo "--version takes <plugin>=<version> for one of: $(join_names "$allowed")" >&2; exit 2; }
    if has "${pin%%=*}" "$carry"; then pinned="$pinned${pinned:+ }${pin%%=*}"; fi
done
requested=$base
pinned=$(known_order "$pinned")
[ -z "$pinned" ] || requested="$requested${requested:+ }$pinned"
extra=
for name in $seated; do
    if ! has "$name" "$requested" && ! has "$name" "$remove"; then extra="$extra${extra:+ }$name"; fi
done
[ -n "$requested$extra" ] || { echo "Nothing to seat: --remove leaves no plugin. To remove them all, run with --uninstall." >&2; exit 2; }

dropping=
for name in $seated; do
    if has "$name" "$remove"; then dropping="$dropping${dropping:+ }$name"; fi
done
kept=$extra
if [ -n "$extra" ] && [ "$plugins_given" -eq 1 ] && [ "$latest" -eq 0 ] && [ -t 0 ] && [ -t 2 ]; then
    printf 'Seated now but not in --plugins: %s. Remove them? Each gets your own copy back. [y/N] ' "$(join_names "$extra")" >&2
    read -r answer </dev/tty || answer=
    case $answer in
        y|Y|yes|Yes|YES) dropping=$(known_order "$dropping $extra"); kept= ;;
    esac
fi
plugins="$requested${kept:+${requested:+ }$kept}"
[ -z "$dropping" ] || echo "Removing seated plugins: $(join_names "$dropping")."
[ -z "$kept" ] || echo "Kept seated plugins outside this run's set: $(join_names "$kept"). To remove one, run again with --remove <name>."

release_versions() {  # $1: plugin -> its release versions, newest first
    git ls-remote --tags --refs "$repository" "$1--v*" \
        | sed -n "s|.*refs/tags/$1--v\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)\$|\1|p" \
        | sort -t. -k1,1nr -k2,2nr -k3,3nr
}

choose_version() {  # $1: plugin, $2: versions newest first -> the chosen one
    shown=$(printf '%s\n' "$2" | head -n 10)
    if command -v fzf >/dev/null 2>&1; then
        picked=$(printf '%s\n' "$shown" | fzf --height=12 --reverse --no-sort \
            --prompt="$1 release> " --header="Enter picks; Esc keeps the latest") || picked=
    else
        echo "Choose the $1 release (Enter keeps the latest):" >&2
        printf '%s\n' "$shown" | awk 'NR == 1 { print "  " NR ") " $0 "  (latest)"; next } { print "  " NR ") " $0 }' >&2
        printf 'Number [1]: ' >&2
        read -r n </dev/tty || n=
        case $n in
            ''|*[!0-9]*) picked= ;;
            *) picked=$(printf '%s\n' "$shown" | sed -n "${n}p") ;;
        esac
    fi
    [ -n "$picked" ] || picked=$(printf '%s\n' "$shown" | head -n 1)
    echo "$picked"
}

release_tag() {  # $1: plugin
    for pin in $pins; do
        [ "${pin%%=*}" = "$1" ] && { echo "$1--v${pin#*=}" | sed 's/--vv/--v/'; return 0; }
    done
    versions=$(release_versions "$1")
    [ -n "$versions" ] || { echo "No release tag $1--v<version> in $repository" >&2; return 1; }
    if [ "$latest" -eq 0 ] && [ -t 0 ] && [ -t 2 ] && [ "$(printf '%s\n' "$versions" | wc -l)" -gt 1 ]; then
        echo "$1--v$(choose_version "$1" "$versions")"
    else
        echo "$1--v$(printf '%s\n' "$versions" | head -n 1)"
    fi
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

src_for() {  # $1: plugin -> the folder holding plugins/$1
    if [ -n "$source_dir" ]; then echo "$source_dir"; else echo "$work/$1"; fi
}

if [ -z "$source_dir" ]; then
    for p in $plugins; do
        tag=$(release_tag "$p")
        git -c advice.detachedHead=false clone --depth 1 --quiet --branch "$tag" "$repository" "$work/$p"
        echo "Fetched $tag"
        echo "$tag" > "$work/$p.tag"
    done
fi

entries=
ids=
for p in $plugins; do
    manifest="$(src_for "$p")/plugins/$p/.claude-plugin/plugin.json"
    [ -f "$manifest" ] || { echo "No plugin '$p' in $(src_for "$p")" >&2; exit 1; }
    version=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" | head -n 1)
    if [ -f "$work/$p.tag" ] && [ "$(cat "$work/$p.tag")" != "$p--v$version" ]; then
        echo "warning: $(cat "$work/$p.tag") holds $p $version in its plugin.json" >&2
    fi
    echo "Source $p: $version"
    entries="$entries${entries:+,
    }{ \"name\": \"$p\", \"source\": \"./plugins/$p\" }"
    ids="$ids${ids:+, }\"$p@$marketplace\""
done

sudo mkdir -p "$target/plugins" "$target/.claude-plugin" "$(dirname "$drop_in")"
for p in $plugins; do
    sudo rsync -a --delete --exclude .git "$(src_for "$p")/plugins/$p/" "$target/plugins/$p/"
done
removed=
for d in "$target"/plugins/*/; do
    [ -d "$d" ] || continue
    name=$(basename "$d")
    case " $plugins " in *" $name "*) ;; *) sudo rm -rf "$d"; removed="$removed $name" ;; esac
done

sudo tee "$target/.claude-plugin/marketplace.json" >/dev/null <<EOF
{
  "name": "$marketplace",
  "owner": { "name": "agentpit-lab", "url": "https://github.com/agentpit-lab" },
  "description": "AgentPit plugins seated as organization plugins from a local copy of the agentpit marketplace.",
  "plugins": [
    $entries
  ]
}
EOF

enabled=$(echo "$ids" | sed 's/"\([^"]*\)"/"\1": true/g')
# Write beside the drop-in, then rename: a truncated drop-in would drop seated plugins on the next rerun.
sudo tee "$drop_in.tmp" >/dev/null <<EOF
{
  "extraKnownMarketplaces": {
    "$marketplace": { "source": { "source": "directory", "path": "$target" } }
  },
  "enabledPlugins": { $enabled },
  "appendPlugins": [ $ids ]
}
EOF
sudo mv -f "$drop_in.tmp" "$drop_in"
echo "Wrote $drop_in"

# The directory and every directory above it must be writable by root only.
sudo chown -R "root:$group" "$target" "$root/managed-settings.d"
sudo chmod -R u+rwX,go+rX,go-w "$target" "$root/managed-settings.d"
dir=$target
while [ "$dir" != / ]; do
    if [ -n "$(find "$dir" -maxdepth 0 \( -perm -g+w -o -perm -o+w -o ! -user root \) 2>/dev/null)" ]; then
        echo "warning: $dir is writable by someone other than root" >&2
    fi
    dir=$(dirname "$dir")
done

for p in $plugins; do
    from="$data_root/$p-$user_marketplace"
    to="$data_root/$p-$marketplace"
    if [ -d "$from" ] && [ ! -e "$to" ]; then
        cp -R "$from" "$to"
        echo "Copied plugin data $from -> $to"
    fi
done

set_user_plugins disable
# A plugin dropped from the set gets its user-scope copy back, which an earlier run disabled.
[ -z "$removed" ] || set_user_plugins enable "$removed"

cat <<EOF

Done. Restart Claude Code sessions, then check:
  /status          -> Setting sources lists Enterprise managed settings
  claude --debug   -> 'hooks module <plugin>@$marketplace loaded (... tier append)' and no 'bypassed by' line
  claude plugin list does not show plugins loaded in place from a managed directory.
EOF
