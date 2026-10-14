#!/command/with-contenv sh
# Let the hermes user open GPU device nodes that umbrelOS maps into the
# container for apps with the GPU permission.
#
# umbrelOS passes /dev/dri (and /dev/kfd for ROCm) with `group_add` set to the
# host GIDs that own those nodes (typically video and render, mode 0660). That
# grants the groups to PID 1 only: upstream drops to hermes via s6-setuidgid,
# which calls initgroups() and rebuilds the supplementary groups from
# /etc/group, so llama.cpp's Vulkan backend would see no GPU. Add matching
# /etc/group entries for hermes, as upstream already does for the Docker socket.
# NVIDIA device nodes are world-accessible and need nothing here.

for node in /dev/dri/* /dev/kfd; do
    [ -c "$node" ] || continue
    gid=$(stat -c '%g' "$node" 2>/dev/null) || continue
    # Never add hermes to root's group; umbrelOS does not map GID 0 either.
    [ -n "$gid" ] && [ "$gid" != 0 ] || continue
    id -G hermes | tr ' ' '\n' | grep -qx "$gid" && continue

    group=$(getent group "$gid" | cut -d: -f1)
    if [ -z "$group" ]; then
        group="hostgpu$gid"
        if ! groupadd -g "$gid" "$group"; then
            echo "[umbrel-gpu] Warning: could not create group $group for $node"
            continue
        fi
    fi
    if usermod -aG "$group" hermes; then
        echo "[umbrel-gpu] Added hermes to group $group (GID $gid) for $node"
    else
        echo "[umbrel-gpu] Warning: could not add hermes to group $group for $node"
    fi
done
